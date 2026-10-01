# ORBIT launcher for Windows (PowerShell 5.1+). The Makefile does the same on macOS/Linux.
# Usage: .\orbit.cmd <command>     (orbit.cmd bypasses the execution policy for this script only)
param(
    [Parameter(Position = 0)] [string] $Command = "help",
    [Parameter(Position = 1)] [string] $Service = ""
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# UI + live simulation only: no MinIO/archiver, EMQX, ML or observability.
$DemoServices = @("redpanda", "timescale", "redis", "topics", "migrate", "seed",
                  "ingest", "state", "alert", "planner", "api", "web", "simulator")
$Ports = @(3000, 8000, 5432, 6379, 19092)

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function Invoke-Compose {
    & docker compose @args
    if ($LASTEXITCODE -ne 0) { Fail "docker compose $($args -join ' ') failed (exit $LASTEXITCODE)" }
}

function Ensure-Env {
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Say "created .env from .env.example"
    }
}

function Set-EnvValue($key, $value) {
    $lines = Get-Content ".env"
    if ($lines -match "^$key=") { $lines = $lines -replace "^$key=.*", "$key=$value" }
    else { $lines += "$key=$value" }
    # UTF-8 without BOM, LF endings: docker compose reads this file.
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), (($lines -join "`n") + "`n"))
}

function Test-Prereqs {
    Say "checking prerequisites..."
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail "Docker is not installed. Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
    }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop is not running. Start it, wait for 'Engine running', then retry." }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) { Fail "'docker compose' (v2) not found. Update Docker Desktop." }

    # Disk: Docker's WSL disk lives on the system drive by default.
    $drive = (Get-Item $env:LOCALAPPDATA).PSDrive.Name
    $freeGb = [math]::Floor((Get-PSDrive $drive).Free / 1GB)
    if ($freeGb -lt 15) { Fail "only $freeGb GB free on ${drive}: - need 15 GB (25 GB recommended). Docker goes read-only when the disk fills." }
    if ($freeGb -lt 25) { Say "warning: $freeGb GB free on ${drive}: (25 GB recommended)" "Yellow" }
    else { Say "disk ok: $freeGb GB free on ${drive}:" "Green" }

    # Memory available to Docker (the WSL2 VM). Pick Redpanda sizing to fit.
    $memBytes = [int64](& docker info --format "{{.MemTotal}}")
    $memGb = [math]::Round($memBytes / 1GB, 1)
    $cpus = [int](& docker info --format "{{.NCPU}}")
    if ($memGb -lt 5) { Fail "Docker has only $memGb GB RAM. Give WSL at least 6 GB (see WINDOWS.md, step 2)." }
    if ($memGb -lt 10 -or $cpus -lt 4) {
        Set-EnvValue "REDPANDA_SMP" "2"
        Set-EnvValue "REDPANDA_MEMORY" "1500M"
        Say "Docker has $memGb GB / $cpus CPUs: using the small Redpanda profile (2 cores, 1.5 GB)" "Yellow"
    } else {
        Set-EnvValue "REDPANDA_SMP" "3"
        Set-EnvValue "REDPANDA_MEMORY" "3G"
        Say "memory ok: Docker has $memGb GB / $cpus CPUs" "Green"
    }

    # Ports already taken by something outside ORBIT (e.g. a local PostgreSQL on 5432).
    $ours = (& docker compose ps -q) -ne $null
    foreach ($p in $Ports) {
        $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        if ($busy -and -not $ours) {
            $proc = (Get-Process -Id $busy[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
            Say "warning: port $p is already used by '$proc'. Stop it, or ORBIT's container on that port will fail." "Yellow"
        }
    }
}

function Wait-Api {
    Say "waiting for the API (first start seeds 100,000 vehicles; can take a few minutes)..."
    for ($i = 0; $i -lt 120; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://localhost:8000/healthz"
            if ($r.StatusCode -eq 200) { Say "API is up" "Green"; return }
        } catch { }
        Start-Sleep -Seconds 5
    }
    Say "API not ready after 10 min. Check: .\orbit.cmd logs api" "Yellow"
}

function Show-Urls {
    Write-Host ""
    Say "  Web console   http://localhost:3000   (sign in: tenant 1, role manager)" "Green"
    Say "  API docs      http://localhost:8000/docs" "Green"
    Say "  Sim logs      .\orbit.cmd logs simulator" "Green"
    Write-Host ""
}

switch ($Command.ToLower()) {
    "demo" {
        Ensure-Env; Test-Prereqs
        Say "building and starting UI + simulation (first build downloads ~6 GB and takes 10-20 min)..."
        Invoke-Compose --profile sim up -d --build @DemoServices
        Wait-Api; Show-Urls
        Start-Process "http://localhost:3000"
    }
    "full" {
        Ensure-Env; Test-Prereqs
        Say "starting everything incl. MinIO, EMQX, archiver and observability (needs 16 GB RAM)..."
        Invoke-Compose --profile sim --profile obs up -d --build
        Wait-Api; Show-Urls
        Say "  Grafana http://localhost:3001  Kafka console http://localhost:8090  EMQX http://localhost:18083" "Green"
    }
    "ml" {
        Say "training range + battery-health models and publishing predictions..."
        Invoke-Compose --profile ml run --rm ml
    }
    "check"     { Ensure-Env; Test-Prereqs }
    "pause-sim" { Invoke-Compose stop simulator; Say "simulator paused; UI stays up" "Green" }
    "resume-sim" { Invoke-Compose --profile sim start simulator }
    "status"    { Invoke-Compose --profile sim ps }
    "logs" {
        if ($Service) { Invoke-Compose logs -f --tail 100 $Service } else { Invoke-Compose logs -f --tail 50 }
    }
    "trim" {
        Say "keeping only the last 6 h of telemetry in TimescaleDB (frees disk)..."
        Get-Content -Raw "db\dev_retention.sql" | & docker compose exec -T timescale psql -U postgres -d orbit -f -
    }
    "stop"  { Invoke-Compose --profile sim --profile obs --profile ml stop; Say "stopped; data kept" "Green" }
    "down"  { Invoke-Compose --profile sim --profile obs --profile ml down; Say "containers removed; data kept" "Green" }
    "reset" {
        $ok = Read-Host "Delete ALL ORBIT data (database, Kafka, Redis volumes)? type yes"
        if ($ok -eq "yes") { Invoke-Compose --profile sim --profile obs --profile ml down -v; Say "all data deleted" "Green" }
        else { Say "cancelled" }
    }
    default {
        Write-Host @"
ORBIT launcher - usage: .\orbit.cmd <command>

  demo         build + start the web UI and live simulation (start here)
  full         everything: + MinIO, EMQX, archiver, Grafana/Prometheus (16 GB RAM)
  ml           train ML models and publish range / battery-health predictions
  check        check Docker, disk, memory and ports only
  status       list containers
  logs [svc]   follow logs (e.g. .\orbit.cmd logs simulator)
  pause-sim    stop the simulator (UI keeps running, disk stops growing)
  resume-sim   start the simulator again
  trim         keep only 6 h of telemetry (free disk)
  stop         stop all containers, keep data
  down         remove containers, keep data
  reset        delete containers AND all data
"@
    }
}
