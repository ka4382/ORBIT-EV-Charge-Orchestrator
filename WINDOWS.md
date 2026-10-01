# Running ORBIT on Windows

Everything runs in Docker, so you do **not** need Python, Go, Node or make installed.

## What you need

| | Minimum | Recommended |
|---|---|---|
| Windows | 10 (22H2) or 11, 64-bit | 11 |
| RAM | 8 GB | 16 GB |
| Free disk on C: | 15 GB | 25 GB |
| Internet | first start downloads ~6 GB | |

## 1. Install Docker Desktop

1. Download and install **Docker Desktop for Windows**: https://www.docker.com/products/docker-desktop/
2. During setup, keep **"Use WSL 2 instead of Hyper-V"** ticked. If it asks to install or update WSL, say yes and reboot.
3. Start Docker Desktop and wait until the bottom-left corner says **Engine running**.

## 2. Give Docker enough memory (8 GB laptops: important)

WSL 2 gives Docker half of your RAM by default. On an 8 GB laptop that is too little.

1. Press `Win + R`, type `%UserProfile%` and press Enter.
2. Create a file named `.wslconfig` (no `.txt` at the end) with:

   ```ini
   [wsl2]
   memory=6GB
   processors=4
   ```

   With 16 GB of RAM or more, use `memory=8GB`.
3. Open PowerShell and run `wsl --shutdown`, then restart Docker Desktop.

## 3. Unzip the project

Unzip to a **short path with no spaces**, for example `C:\orbit`. Long paths such as
`C:\Users\<name>\Downloads\orbit-project\orbit-project\...` can hit Windows' 260-character path limit.

## 4. Start the UI and the live simulation

Open **PowerShell** in the project folder (in Explorer: `Shift + right-click` the folder, then
**Open in Terminal** or **Open PowerShell window here**) and run:

```powershell
.\orbit.cmd demo
```

The first run builds the images, which takes **10 to 20 minutes**. Later starts take under a minute. The script:

- checks that Docker is running and that there is enough disk, memory and free ports;
- picks Redpanda settings that fit your RAM;
- starts the stack, waits for the API and opens **http://localhost:3000** in your browser.

To sign in, the form is pre-filled with **tenant 1, role manager**, so just press **Sign in**.

| Page | What you see |
|---|---|
| `/console` | live map of 5,000 vehicles driving and charging, fleet totals updating every second |
| `/console/vehicles` | every vehicle's charge, battery health and state |
| `/console/alerts` | low-battery, fault-code and charging-stopped alerts as they happen |
| `/console/schedule` | cheapest-tariff charging plans |
| `/console/reports` | energy and cost charts |
| `/console/agent` | ask questions about the fleet |
| http://localhost:8000/docs | REST API (Swagger) |

## Strong laptop (16 GB+ RAM): the full experience

1. In `.wslconfig` (step 2) use `memory=14GB` and `processors=8`, then `wsl --shutdown` and restart Docker Desktop.
2. Optional, for a bigger fleet: edit `.env` and set `SIM_VEHICLES=20000`, which gives 20,000 events/s with 60,000/s bursts.
3. Start everything, then train the ML models:

   ```powershell
   .\orbit.cmd full     # UI, simulation, archiver, MinIO, EMQX, Grafana, Prometheus, Loki, Tempo
   .\orbit.cmd ml       # range + battery-health predictions show up in the console
   ```

| Extra surface | URL | Login |
|---|---|---|
| Grafana dashboards | http://localhost:3001 | admin / orbit-admin |
| Kafka (Redpanda) console | http://localhost:8090 | |
| EMQX (MQTT) dashboard | http://localhost:18083 | admin / orbit-admin |
| MinIO (Parquet lake) | http://localhost:9001 | orbit / orbit-secret |

`full` stores telemetry history, which is what the Reports page needs, so the database grows over time.
Run `.\orbit.cmd trim` now and then, or `pause-sim` when you are not demoing.

## Everyday commands

```powershell
.\orbit.cmd status            # what is running
.\orbit.cmd logs simulator    # watch events/s from the simulator (Ctrl+C to exit)
.\orbit.cmd pause-sim         # stop the simulation (UI stays up, disk stops growing)
.\orbit.cmd resume-sim        # start it again
.\orbit.cmd ml                # train the ML models (range + battery health), takes a few minutes
.\orbit.cmd trim              # keep only the last 6 h of telemetry to free disk
.\orbit.cmd stop              # stop everything, keep data
.\orbit.cmd reset             # delete all data and start fresh next time
.\orbit.cmd full              # everything incl. Grafana, MinIO, EMQX (needs 16 GB RAM)
```

> `demo` does not store raw telemetry history (the archiver is off), and Kafka keeps at most 30 minutes, so
> disk use stays roughly flat. `full` writes history to the database and grows over time: run `trim` or
> `pause-sim` when you are not demoing. If Docker's disk fills up, Docker turns read-only.
> The **Reports** charts are built from that history, so they fill in only with `full`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Docker Desktop is not running` | Start Docker Desktop and wait for **Engine running**. |
| `port 5432 is already used by 'postgres'` | A local PostgreSQL is running. Stop it in `services.msc` (postgresql-x64-…), or uninstall it. The same applies to 3000, 8000 and 6379. |
| Build fails with `TLS handshake timeout` or network errors | Retry `.\orbit.cmd demo`. Docker resumes where it stopped. A VPN or proxy can cause this. |
| Page loads but the map is empty | Wait 30 s after the first start. Check that `.\orbit.cmd logs simulator` shows `events/s=...`. |
| Containers keep restarting (`Exited (137)`) | Not enough memory. Increase `memory=` in `.wslconfig` (step 2) or close other apps. |
| `running scripts is disabled on this system` | Use `.\orbit.cmd ...` (not `.\orbit.ps1`). It bypasses the policy for this script only. |
| Want a smaller fleet | Edit `.env`: `SIM_VEHICLES=2000`, then `.\orbit.cmd demo` again. |

## macOS / Linux

Use the Makefile instead: `make demo` (UI + simulation) or `make run` (everything).
