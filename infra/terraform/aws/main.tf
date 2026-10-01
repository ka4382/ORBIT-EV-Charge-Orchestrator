# ORBIT on AWS (ap-south-1): EKS for stateless services, MSK for Kafka, RDS Postgres (Timescale via self-managed
# on EKS or Timescale Cloud), ElastiCache Redis, S3 for Parquet. Apply: terraform init && terraform apply.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
  }
  backend "s3" {} # configured via -backend-config
}

provider "aws" { region = var.region }

locals { name = "orbit-${var.env}" }

module "vpc" {
  source             = "terraform-aws-modules/vpc/aws"
  version            = "~> 5.13"
  name               = local.name
  cidr               = "10.40.0.0/16"
  azs                = ["${var.region}a", "${var.region}b", "${var.region}c"]
  private_subnets    = ["10.40.1.0/24", "10.40.2.0/24", "10.40.3.0/24"]
  public_subnets     = ["10.40.101.0/24", "10.40.102.0/24", "10.40.103.0/24"]
  enable_nat_gateway = true
  single_nat_gateway = var.env != "prod"
}

module "eks" {
  source                                   = "terraform-aws-modules/eks/aws"
  version                                  = "~> 20.24"
  cluster_name                             = local.name
  cluster_version                          = "1.31"
  vpc_id                                   = module.vpc.vpc_id
  subnet_ids                               = module.vpc.private_subnets
  cluster_endpoint_public_access           = true
  enable_cluster_creator_admin_permissions = true
  eks_managed_node_groups = {
    general = { instance_types = ["m6i.xlarge"], min_size = 3, max_size = 20, desired_size = 3 }
  }
}

resource "aws_msk_cluster" "kafka" {
  cluster_name           = local.name
  kafka_version          = "3.6.0"
  number_of_broker_nodes = 3
  broker_node_group_info {
    instance_type   = var.env == "prod" ? "kafka.m5.xlarge" : "kafka.t3.small"
    client_subnets  = module.vpc.private_subnets
    security_groups = [aws_security_group.kafka.id]
    storage_info {
      ebs_storage_info {
        volume_size = 500
      }
    }
  }
  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
    }
  }
  client_authentication {
    sasl {
      iam = true
    }
  }
}

resource "aws_security_group" "kafka" {
  name   = "${local.name}-kafka"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port   = 9096
    to_port     = 9098
    protocol    = "tcp"
    cidr_blocks = [module.vpc.vpc_cidr_block]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_subnet_group" "pg" {
  name       = local.name
  subnet_ids = module.vpc.private_subnets
}
resource "aws_db_instance" "pg" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.env == "prod" ? "db.r6g.2xlarge" : "db.t4g.medium"
  allocated_storage            = 200
  storage_encrypted            = true
  db_name                      = "orbit"
  username                     = "postgres"
  manage_master_user_password  = true
  db_subnet_group_name         = aws_db_subnet_group.pg.name
  vpc_security_group_ids       = [aws_security_group.pg.id]
  skip_final_snapshot          = var.env != "prod"
  performance_insights_enabled = true
}
resource "aws_security_group" "pg" {
  name   = "${local.name}-pg"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [module.vpc.vpc_cidr_block]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = local.name
  subnet_ids = module.vpc.private_subnets
}
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = local.name
  description                = "ORBIT live state"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.env == "prod" ? "cache.r6g.large" : "cache.t4g.small"
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
}

resource "aws_s3_bucket" "telemetry" { bucket = "${local.name}-telemetry" }
resource "aws_s3_bucket_server_side_encryption_configuration" "telemetry" {
  bucket = aws_s3_bucket.telemetry.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "telemetry" {
  bucket = aws_s3_bucket.telemetry.id
  rule {
    id     = "hot-warm-cold"
    status = "Enabled"
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    } # warm
    transition {
      days          = 180
      storage_class = "GLACIER_IR"
    }                         # cold
    expiration { days = 730 } # retention: 2 years
  }
}
