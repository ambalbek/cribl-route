# Cribl Pusher

Automates adding **routes** and upserting **destinations** (blob storage outputs) across Cribl workspaces. Supports single-app and bulk-file modes with a full diff preview, safety guards, and automatic rollback snapshots before every write.

Also includes **`rode_rm.py`** — a companion script that pushes **ELK roles + role-mappings** and **Cribl routes/destinations** together in a single run, with configurable ordering and per-side skip flags.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Prerequisites](#prerequisites)
3. [File Structure](#file-structure)
4. [First-Time Setup](#first-time-setup)
5. [Configuration Reference](#configuration-reference)
6. [Template Files](#template-files)
7. [App Input Format](#app-input-format)
8. [Running the Script](#running-the-script)
9. [rode_rm.py — ELK Roles + Cribl](#rode_rmpy--elk-roles--cribl)
10. [Web UI](#web-ui)
11. [Docker](#docker)
12. [All CLI Flags](#all-cli-flags)
13. [Logging](#logging)
14. [Safety Features](#safety-features)
15. [Rolling Back a Change](#rolling-back-a-change)
16. [Troubleshooting](#troubleshooting)

---

## What It Does

For each application you provide (by ID and name), the script:

1. Fetches the current route table from Cribl (`GET /api/v1/m/{worker_group}/routes/{routes_table}`)
2. Fetches all existing destinations (`GET /system/outputs`) to build a skip-list
3. Inserts a new route above the catch-all/default route — skipping any that already exist
4. Shows a full unified diff so you can review exactly what will change
5. Asks for confirmation before writing anything
6. Saves a rollback snapshot of the original route table
7. Creates any destination that does not already exist (`POST /system/outputs`) — skips if present
8. Patches the route table back to Cribl (`PATCH /api/v1/m/{worker_group}/routes/{routes_table}`)

Each workspace can point to a **different Cribl cluster** via an optional per-workspace `base_url`, or you can override the URL at runtime with `--cribl-url`.

---

## Prerequisites

- **Python 3.10 or newer** *(not needed if running via Docker)*
- **Docker Desktop** *(optional — for the containerised option)*
- **pip** packages:

```bash
# CLI only
pip install requests urllib3 jinja2

# CLI + web UI
pip install requests urllib3 jinja2 streamlit
```

Verify your Python version:

```bash
python --version
# Should print Python 3.10.x or higher
```

---

## File Structure

```
cribl-rout/
│
├── cribl-pusher.py              # CLI entry point — run this
├── rode_rm.py                   # Companion CLI — pushes ELK roles + Cribl routes together
├── ui.py                        # Streamlit web UI (two tabs) — run with: streamlit run ui.py
├── cribl_api.py                 # Cribl API + route logic
├── cribl_config.py              # Config loading and workspace resolution
├── cribl_utils.py               # Shared utilities (I/O, prompts, HTTP session)
├── cribl_logger.py              # Logging setup
│
├── Dockerfile                   # Container image definition
├── .dockerignore                # Files excluded from the Docker build context
├── requirements.txt             # Pip dependencies
│
├── config.json                  # YOUR config (credentials + workspaces) — never commit
├── config.example.json          # Safe-to-commit template — copy this to config.json
│
├── route_template.json          # Route shape used for every new route  ← you must create
├── blob_dest_template_dev.json  # Destination shape for the dev workspace  ← you must create
├── blob_dest_template_qa.json   # Destination shape for the qa workspace   ← you must create
├── blob_dest_template_prod.json # Destination shape for the prod workspace ← you must create
│
├── appids.txt                   # (optional) Bulk app list — one "appid,appname" per line
│
├── ops_rm_r_templates_output/   # Auto-created by rode_rm.py — ELK template files saved here
│
└── cribl_snapshots/             # Auto-created — rollback snapshots saved here
    ├── dev/
    ├── qa/
    └── prod/
```

> `config.json` and `cribl_snapshots/` are in `.gitignore` and will never be committed.

---

## First-Time Setup

### Step 1 — Clone / copy the files

Make sure all `.py` files, template `.json` files, and `config.example.json` are in the same folder.

### Step 2 — Install dependencies

```bash
pip install requests urllib3 jinja2 streamlit
```

### Step 3 — Create your config file

```bash
# Windows
copy config.example.json config.json

# Mac / Linux
cp config.example.json config.json
```

### Step 4 — Edit config.json

Open `config.json` in any text editor and fill in:

| Field | What to put |
|---|---|
| `base_url` | Default Cribl URL, e.g. `https://cribl.company.com:9000` |
| `cribl_urls` | List of Cribl URLs shown as a dropdown in the UI |
| `elk_urls` | List of ELK URLs shown as a dropdown in the UI |
| `credentials.username` | Your Cribl login username |
| `credentials.password` | Your Cribl login password (or leave blank to type it at runtime) |
| `credentials.token` | A pre-generated bearer token — if set, username/password are ignored |
| `workspaces` | One entry per worker group you want to target (see below) |

**Example with multiple clusters:**

```json
{
  "base_url": "https://cribl-azn.company.com:9000",
  "cribl_urls": [
    "https://cribl-azn.company.com:9000",
    "https://cribl-azs.company.com:9000"
  ],
  "elk_urls": [
    "https://elk-azn.company.com:9200",
    "https://elk-azs.company.com:9200"
  ],
  "skip_ssl": false,
  "credentials": {
    "token": "",
    "username": "admin",
    "password": "yourpassword"
  },
  "route_template": "route_template.json",
  "snapshot_dir": "cribl_snapshots",
  "min_existing_total_routes": 1,
  "diff_lines": 3,
  "workspaces": {
    "azn-dev": {
      "base_url": "https://cribl-azn.company.com:9000",
      "worker_group": "dev",
      "dest_template": "blob_dest_template_dev.json",
      "description": "Azure North — Dev"
    },
    "azs-dev": {
      "base_url": "https://cribl-azs.company.com:9000",
      "worker_group": "dev",
      "dest_template": "blob_dest_template_dev.json",
      "description": "Azure South — Dev"
    },
    "azn-prod": {
      "base_url": "https://cribl-azn.company.com:9000",
      "worker_group": "prod",
      "dest_template": "blob_dest_template_prod.json",
      "description": "Azure North — Prod",
      "require_allow": true
    }
  }
}
```

### Step 5 — Create the template files

The following files must exist in the same folder. Grab the shapes from your live Cribl instance:

**`route_template.json`** — fetch a route from Cribl and strip out the app-specific fields:

```bash
curl -k -H "Authorization: Bearer YOUR_TOKEN" \
  "https://YOUR_CRIBL:9000/api/v1/m/{worker_group}/routes/{routes_table}"
```

Minimum working example:

```json
{
  "pipeline": "passthru",
  "final": false,
  "disabled": false,
  "clones": [],
  "description": "",
  "enableOutputExpression": false
}
```

**`blob_dest_template_*.json`** — fetch an existing output and strip the app-specific fields:

```bash
curl -k -H "Authorization: Bearer YOUR_TOKEN" \
  "https://YOUR_CRIBL:9000/api/v1/m/{worker_group}/system/outputs/{output_id}"
```

The script fills in `id`, `name`, `containerName`, and `description` automatically.

### Step 6 — Do a dry run

```bash
python cribl-pusher.py --workspace azn-dev --dry-run --appid TEST001 --appname "Test App"
```

You should see the `=== TARGET ===` banner and a diff preview with no errors. **Nothing is written on a dry run.**

---

## Configuration Reference

### Top-level fields

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | — | Default Cribl root URL (overridden per workspace or via `--cribl-url`) |
| `cribl_urls` | list | `[]` | Cribl URLs shown as a dropdown in the UI Cribl Pusher tab |
| `elk_urls` | list | `[]` | ELK URLs shown as a dropdown in the UI ELK Roles + Cribl tab |
| `skip_ssl` | bool | `false` | Disable SSL cert verification globally |
| `credentials.token` | string | `""` | Bearer token — if set, skips username/password login |
| `credentials.username` | string | `""` | Login username |
| `credentials.password` | string | `""` | Login password |
| `route_template` | string | `route_template.json` | Default route template path |
| `snapshot_dir` | string | `cribl_snapshots` | Directory where rollback snapshots are saved |
| `min_existing_total_routes` | int | `1` | Refuse to PATCH if fewer than this many routes are loaded |
| `diff_lines` | int | `3` | Lines of context shown in the diff preview |

### Workspace fields

Each key under `workspaces` is a name you choose (e.g. `"azn-dev"`, `"azs-prod"`).

| Field | Required | Description |
|---|---|---|
| `worker_group` | yes | Cribl worker group name — forms the API path `/api/v1/m/{worker_group}` |
| `dest_template` | yes | Path to the destination template JSON for this workspace |
| `base_url` | no | Overrides the global `base_url` — use this to point a workspace at a different cluster |
| `routes_table` | no | Route table name in `GET/PATCH /routes/{routes_table}`. Defaults to `worker_group` |
| `description` | no | Human-readable label shown in the run banner and UI dropdown |
| `require_allow` | no | If `true`, user must type `ALLOW` before any writes (recommended for prod) |
| `skip_ssl` | no | Overrides the global `skip_ssl` for this workspace only |
| `route_template` | no | Overrides the global `route_template` for this workspace only |

### Credential priority (highest to lowest)

```
1. --token / --username / --password  CLI flags
2. CRIBL_TOKEN / CRIBL_USERNAME / CRIBL_PASSWORD  environment variables
3. credentials block in config.json
```

---

## Template Files

### route_template.json

Defines the shape of every new route. The script fills in `id`, `filter`, `output`, and `name` for each app automatically. All other fields come from this template.

### blob_dest_template_*.json

Defines the shape of the blob storage destination for each workspace. The script fills in `id`, `name`, `containerName`, and `description` automatically.

---

## App Input Format

### Single app — via CLI flags

```bash
python cribl-pusher.py --appid APP001 --appname "My Application"
```

### Bulk apps — via text file

Create a file (default name: `appids.txt`) with one app per line:

```
# Lines starting with # are comments and are ignored
APP001, My First Application
APP002, My Second Application
APP003, Another App
```

Rules:
- Format is `appid, appname` (comma-separated)
- Leading/trailing spaces are trimmed
- Blank lines and `#` comments are skipped
- Both fields are required

---

## Running the Script

### Option A — Web UI (recommended)

```bash
streamlit run ui.py
```

Opens `http://localhost:8501`. See the [Web UI](#web-ui) section for details.

---

### Option B — CLI (single app)

```bash
python cribl-pusher.py \
  --workspace azn-dev \
  --appid APP001 \
  --appname "My Application" \
  --yes
```

---

### Option C — CLI (bulk file)

```bash
python cribl-pusher.py \
  --workspace azn-dev \
  --from-file \
  --appfile appids.txt \
  --yes
```

---

### Dry run (preview only — no writes)

```bash
python cribl-pusher.py --workspace azn-dev --dry-run --from-file --appfile appids.txt
```

---

### Override the Cribl URL at runtime

```bash
python cribl-pusher.py \
  --cribl-url https://cribl-azs.company.com:9000 \
  --workspace azs-dev \
  --appid APP001 --appname "My App" \
  --yes
```

---

### Production workspace

Workspaces with `"require_allow": true` require an extra flag:

```bash
python cribl-pusher.py \
  --workspace azn-prod \
  --allow-prod \
  --from-file --appfile appids.txt \
  --yes
```

---

### Using a route group

```bash
python cribl-pusher.py \
  --workspace azn-dev \
  --group-id my-group-id \
  --create-missing-group \
  --group-name "My New Group" \
  --from-file
```

---

## rode_rm.py — ELK Roles + Cribl

`rode_rm.py` applies **ELK roles/role-mappings** and **Cribl routes/destinations** in a single command. Both sides can run together or independently.

### What it does

1. Generates ELK role and role-mapping templates (always saved to `ops_rm_r_templates_output/`)
2. (ELK side) Pushes roles and role-mappings to Elasticsearch via `PUT /_security/role/{name}` and `PUT /_security/role_mapping/{name}`
3. (Cribl side) Runs the same route + destination upsert logic as `cribl-pusher.py`
4. Runs the two sides in the configured order (`elk-first` by default)

### Generated ELK templates

Every run saves four files to `ops_rm_r_templates_output/`:

| File | Description |
|---|---|
| `roles_{apmid}.json` | Kibana Dev Console format (for human review) |
| `role_mappings_{apmid}.json` | Kibana Dev Console format (for human review) |
| `roles_{apmid}_pushable.json` | JSON array with `method`/`path`/`body` — ready to push via API |
| `role_mappings_{apmid}_pushable.json` | JSON array with `method`/`path`/`body` — ready to push via API |

### Basic usage

```bash
python rode_rm.py \
  --app_name "My Application" \
  --apmid    "app00001234" \
  --elk-url  "https://elk.company.com:9200" \
  --elk-user elastic \
  --elk-password secret \
  --workspace azn-dev \
  --dry-run
```

### Generate templates only (no API calls)

```bash
python rode_rm.py \
  --app_name "My Application" \
  --apmid    "app00001234" \
  --skip-elk \
  --skip-cribl
```

### Override the Cribl URL

```bash
python rode_rm.py \
  --app_name "My App" --apmid "app00001234" \
  --elk-url "https://elk.company.com:9200" --elk-user elastic \
  --cribl-url "https://cribl-azs.company.com:9000" \
  --workspace azs-dev
```

### CLI flags

| Flag | Default | Description |
|---|---|---|
| `--app_name` | *(required)* | Application name |
| `--apmid` | *(required)* | App ID (lower-case, e.g. `app00001234`) |
| `--elk-url` | *(required unless --skip-elk)* | ELK/OpenSearch base URL |
| `--elk-user` | `""` | ELK username (basic auth) |
| `--elk-password` | `""` | ELK password |
| `--elk-token` | `""` | ELK API key — overrides user/password |
| `--cribl-url` | `""` | Cribl base URL override |
| `--workspace` | *(required unless --skip-cribl)* | Cribl workspace name |
| `--allow-prod` | false | Skip the ALLOW prompt for protected workspaces |
| `--order` | `elk-first` | Execution order: `elk-first` or `cribl-first` |
| `--skip-elk` | false | Skip the ELK side (templates are still saved) |
| `--skip-cribl` | false | Skip the Cribl side |
| `--dry-run` | false | Preview only — no writes on either side |
| `--skip-ssl` | false | Disable SSL verification for all connections |
| `--log-level` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--yes` | false | Skip the confirmation prompt |

---

## Web UI

```bash
streamlit run ui.py
```

Opens `http://localhost:8501`. The UI has two tabs:

### Tab 1 — Cribl Pusher

- **Cribl URL** — select from the `cribl_urls` list in config (or type a custom URL if the list is empty)
- **Workspace** — select from workspaces defined in config
- **App Input** — single app (App ID + App Name) or bulk file upload
- **Options** — Dry Run (default: on), Skip SSL, Log Level
- **Credentials override** — Bearer Token or Username/Password (leave blank to use config.json)
- **Advanced Options** — Route Group ID, safety overrides, snapshot directory, log file

### Tab 2 — ELK Roles + Cribl

- **App ID + App Name** — used for both ELK role names and Cribl route/destination
- **ELK URL** — select from the `elk_urls` list in config (or type a custom URL)
- **ELK credentials** — API token or username/password
- **Cribl URL** — select from the `cribl_urls` list in config (or type a custom URL)
- **Workspace** — select from workspaces defined in config
- **Options** — Dry Run (default: on), Skip SSL, Log Level, Order (ELK first / Cribl first)
- **Skip sides** — Skip ELK or Skip Cribl independently

> **Dry Run defaults to ON** in both tabs. Uncheck it to perform actual writes.

Sensitive fields (passwords, tokens) are masked in the command preview shown before each run.

---

## Docker

The image is built on `python:3.13-slim` (linux/amd64). `config.json` and all template JSONs are **never baked in** — they are volume-mounted at runtime.

### Build

```bash
docker build -t cribl-pusher .
```

### Run

```bash
# Linux / macOS / Git Bash
docker run -p 8501:8501 \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/route_template.json:/app/route_template.json:ro \
  -v $(pwd)/blob_dest_template_dev.json:/app/blob_dest_template_dev.json:ro \
  -v $(pwd)/blob_dest_template_prod.json:/app/blob_dest_template_prod.json:ro \
  cribl-pusher
```

```powershell
# Windows PowerShell
docker run -p 8501:8501 `
  -v ${PWD}/config.json:/app/config.json:ro `
  -v ${PWD}/route_template.json:/app/route_template.json:ro `
  -v ${PWD}/blob_dest_template_dev.json:/app/blob_dest_template_dev.json:ro `
  -v ${PWD}/blob_dest_template_prod.json:/app/blob_dest_template_prod.json:ro `
  cribl-pusher
```

Then open `http://localhost:8501`.

### Save, Split, and Transfer

```bash
# Export and split into 25 MB chunks
docker save cribl-pusher:latest -o cribl-pusher.tar
split -b 25m cribl-pusher.tar cribl-pusher.part.
sha256sum cribl-pusher.tar > cribl-pusher.tar.sha256
```

Transfer all `cribl-pusher.part.*` files to the target machine, then:

```bash
cat cribl-pusher.part.* > cribl-pusher.tar
sha256sum -c cribl-pusher.tar.sha256
docker load -i cribl-pusher.tar
```

---

## All CLI Flags

### cribl-pusher.py

| Flag | Default | Description |
|---|---|---|
| `--config` | `config.json` | Path to the config file |
| `--cribl-url` | `""` | Cribl base URL override (overrides config + workspace `base_url`) |
| `--workspace` | *(prompts)* | Workspace name (must match a key in config `workspaces`) |
| `--allow-prod` | false | Skip the ALLOW prompt for workspaces with `require_allow: true` |
| `--token` | `""` | Bearer token override |
| `--username` | `""` | Username override |
| `--password` | `""` | Password override |
| `--skip-ssl` | false | Disable SSL verification |
| `--dry-run` | false | Preview only — no API writes |
| `--yes` | false | Skip the final `YES` confirmation prompt |
| `--appid` | *(prompts)* | Single app ID |
| `--appname` | *(prompts)* | Single app name (required with `--appid`) |
| `--from-file` | false | Load apps from a file |
| `--appfile` | `appids.txt` | Path to the apps file |
| `--group-id` | `""` | Insert routes into this route-group ID |
| `--create-missing-group` | false | Create the group if it doesn't exist |
| `--group-name` | `""` | Display name when creating a missing group |
| `--min-existing-total-routes` | *(from config)* | Override the safety minimum route count |
| `--diff-lines` | *(from config)* | Lines of context in the diff preview |
| `--snapshot-dir` | *(from config)* | Override the snapshot directory |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--log-file` | `""` | Append logs to this file in addition to the console |

---

## Logging

All output uses Python's `logging` module via the shared `"cribl"` logger.

### Log levels

| Level | What you see |
|---|---|
| `ERROR` | Only errors and fatal messages |
| `WARNING` | Errors + warnings |
| `INFO` | Normal run output — targets, plan, OK/SKIP/SNAPSHOT lines *(default)* |
| `DEBUG` | Everything above + each HTTP verb/URL + per-route detail |

```bash
# Write logs to a file (appended across runs)
python cribl-pusher.py --workspace azn-dev --log-file audit.log --from-file --yes
```

---

## Safety Features

| Guard | What it does |
|---|---|
| **Diff preview** | Always shows a full unified diff before asking for confirmation |
| **Minimum routes check** | Refuses to PATCH if the API returns fewer routes than `min_existing_total_routes` |
| **No-shrink check** | Refuses to PATCH if the new total route count is less than the current count |
| **Duplicate skip** | Skips any app whose route name or filter already exists |
| **require_allow** | Protected workspaces require typing `ALLOW` or passing `--allow-prod` |
| **Dry run** | Runs the full logic (auth + GET) but never calls POST or PATCH |
| **Rollback snapshot** | Original route object saved to `cribl_snapshots/{workspace}/` before every PATCH |

---

## Rolling Back a Change

Find the snapshot file printed in the run output:

```
[SNAPSHOT] cribl_snapshots/azn-prod/routes_snapshot_20240315T143022Z.json
```

Restore it using the `routes_url` from the `=== TARGET ===` banner:

```bash
curl -k -X PATCH \
  "https://YOUR_CRIBL:9000/api/v1/m/{worker_group}/routes/{routes_table}" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d @cribl_snapshots/azn-prod/routes_snapshot_20240315T143022Z.json
```

---

## Troubleshooting

### `Config file not found: config.json`

```bash
copy config.example.json config.json   # Windows
cp config.example.json config.json     # Mac/Linux
```

---

### `FileNotFoundError: route_template.json`

The template files are not created automatically. See [Step 5 — Create the template files](#step-5--create-the-template-files).

---

### `[ERR] login failed: 401`

- Wrong username/password in `config.json`
- Or use a token: generate one in Cribl UI under **Settings → API tokens** and set `credentials.token`

---

### `SSL: CERTIFICATE_VERIFY_FAILED`

```json
"skip_ssl": true
```

Or pass `--skip-ssl` at runtime.

---

### `[SAFETY] Refusing to PATCH: total_before=0 < min=1`

The GET returned an empty route table. Check `base_url`, `worker_group`, and that your token has permission to read routes.

---

### `json.decoder.JSONDecodeError` when running rode_rm.py

The ELK template body failed to parse. This usually means the Jinja2 template rendered invalid JSON. Run with `--skip-elk --skip-cribl` first to generate and inspect the template files in `ops_rm_r_templates_output/`.

---

### `ModuleNotFoundError: No module named 'jinja2'`

```bash
pip install jinja2
```

---

### `ModuleNotFoundError: No module named 'requests'`

```bash
pip install requests urllib3
```

---

### Streamlit UI shows a blank right panel after clicking Run

The script likely exited with an error before producing output. Check:
- `config.json` has the correct `base_url` and credentials
- The workspace's `dest_template` file exists
- Enable **Debug** log level in the UI for detailed HTTP output

---

### Docker container can't reach Cribl

If Cribl is running on the same host machine, use `host.docker.internal` instead of `localhost`:

```json
"base_url": "https://host.docker.internal:9000"
```
