# Parity skeleton for GCP: GKE Autopilot + Managed Service for Apache Kafka + Cloud SQL Postgres + Memorystore + GCS.
# The Helm chart is identical; only values-gke.yaml differs. Provided to demonstrate cloud-agnostic deployment.
terraform {
  required_version = ">= 1.6"
  required_providers { google = { source = "hashicorp/google", version = "~> 6.5" } }
}
provider "google" {
  project = var.project
  region  = var.region
}
variable "project" { type = string }
variable "region" {
  type    = string
  default = "asia-south1"
}
variable "env" {
  type    = string
  default = "dev"
}

resource "google_container_cluster" "gke" {
  name                = "orbit-${var.env}"
  location            = var.region
  enable_autopilot    = true
  deletion_protection = false
}
resource "google_sql_database_instance" "pg" {
  name             = "orbit-${var.env}"
  database_version = "POSTGRES_16"
  region           = var.region
  settings { tier = "db-custom-2-8192" }
  deletion_protection = false
}
resource "google_redis_instance" "redis" {
  name           = "orbit-${var.env}"
  tier           = "STANDARD_HA"
  memory_size_gb = 2
  region         = var.region
}
resource "google_storage_bucket" "telemetry" {
  name     = "orbit-${var.env}-telemetry"
  location = var.region
  lifecycle_rule {
    condition { age = 90 }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }
  lifecycle_rule {
    condition { age = 180 }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }
  lifecycle_rule {
    condition { age = 730 }
    action { type = "Delete" }
  }
}
