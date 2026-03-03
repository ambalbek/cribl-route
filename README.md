# Cribl Pusher

Automates adding **routes** and upserting **destinations** (blob storage outputs) across Cribl workspaces. Supports single-app and bulk-file modes with a full diff preview, safety guards, and automatic rollback snapshots before every write.

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
9. [All CLI Flags](#all-cli-flags)
10. [Common Scenarios](#common-scenarios)
11. [Safety Features](#safety-features)
12. [Rolling Back a Change](#rolling-back-a-change)
13. [Troubleshooting](#troubleshooting)

---

## What It Does

For each application you provide (by ID and name), the script:

1. Reads the current route table from Cribl (`GET /routes/default`)
2. Inserts a new route above the catch-all/default route — skipping any that already exist
3. Shows a full unified diff so you can review exactly what will change
4. Asks for confirmation before writing anything
5. Saves a rollback snapshot of the original route table
6. Creates or updates the blob storage destination (`POST/PATCH /system/outputs`)
7. Patches the route table back to Cribl (`PATCH /routes/default`)

Everything targets a **single Cribl URL** with multiple named **workspaces** (worker groups), configured in `config.json`.

---

## Prerequisites

- **Python 3.10 or newer**
- **pip** packages:

```bash
pip install requests urllib3
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
├── cribl-pusher.py              # Entry point — run this
├── cribl_api.py                 # Cribl API + route logic
├── cribl_config.py              # Config loading and workspace resolution
├── cribl_utils.py               # Shared utilities (I/O, prompts, HTTP session)
│
├── config.json                  # YOUR config (credentials + workspaces) — never commit
├── config.example.json          # Safe-to-commit template — copy this to config.json
│
├── route_template.json          # Route shape used for every new route
├── blob_dest_template_dev.json  # Destination shape for the dev workspace
├── blob_dest_template_qa.json   # Destination shape for the qa workspace
├── blob_dest_template_prod.json # Destination shape for the prod workspace
│
├── appids.txt                   # (optional) Bulk app list — one "appid,appname" per line
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
pip install requests urllib3
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
| `base_url` | Your Cribl hostname, e.g. `https://cribl.company.com:9000` |
| `credentials.username` | Your Cribl login username |
| `credentials.password` | Your Cribl login password (or leave blank to type it at runtime) |
| `credentials.token` | A pre-generated bearer token — if set, username/password are ignored |
| `workspaces` | One entry per worker group you want to target (see below) |

**Minimal example:**

```json
{
  "base_url": "https://cribl.company.com:9000",
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
    "dev": {
      "worker_group": "dev",
      "dest_template": "blob_dest_template_dev.json",
      "description": "Development"
    },
    "prod": {
      "worker_group": "prod",
      "dest_template": "blob_dest_template_prod.json",
      "description": "Production",
      "require_allow": true
    }
  }
}
```

### Step 5 — Verify the templates exist

The following files must be present in the same folder as the script:

- `route_template.json`
- `blob_dest_template_dev.json`
- `blob_dest_template_qa.json`
- `blob_dest_template_prod.json`

### Step 6 — Do a dry run

```bash
python cribl-pusher.py --workspace dev --dry-run --appid TEST001 --appname "Test App"
```

You should see the `=== TARGET ===` banner and a diff preview with no errors. **Nothing is written on a dry run.**

---

## Configuration Reference

### Top-level fields

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | — | **Required.** Cribl root URL, e.g. `https://host:9000` |
| `skip_ssl` | bool | `false` | Disable SSL cert verification globally (equivalent to `curl -k`) |
| `credentials.token` | string | `""` | Bearer token. If set, skips username/password login |
| `credentials.username` | string | `""` | Login username |
| `credentials.password` | string | `""` | Login password |
| `route_template` | string | `route_template.json` | Default route template path |
| `snapshot_dir` | string | `cribl_snapshots` | Directory where rollback snapshots are saved |
| `min_existing_total_routes` | int | `1` | Refuse to PATCH if fewer than this many routes are loaded (prevents accidental wipe) |
| `diff_lines` | int | `3` | Lines of context shown in the diff preview |

### Workspace fields

Each key under `workspaces` is a name you choose (e.g. `"dev"`, `"prod"`).

| Field | Required | Description |
|---|---|---|
| `worker_group` | yes | Cribl worker group name — forms the API path `/api/v1/m/{worker_group}` |
| `dest_template` | yes | Path to the destination template JSON for this workspace |
| `description` | no | Human-readable label shown in the run banner |
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

Defines the shape of every new route. The script fills in `id`, `filter`, `output`, and `name` for each app automatically.

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

### Fully interactive (recommended for first use)

```bash
python cribl-pusher.py
```

The script will prompt you for:
1. Workspace (choose from the list in config.json)
2. Mode: single app or file
3. App ID and name (if single mode)
4. Username/password (if not set in config)
5. Final YES confirmation before writing

---

### Single app, non-interactive

```bash
python cribl-pusher.py \
  --workspace dev \
  --appid APP001 \
  --appname "My Application" \
  --yes
```

---

### Bulk mode from file

```bash
python cribl-pusher.py \
  --workspace qa \
  --from-file \
  --appfile appids.txt \
  --yes
```

---

### Dry run (preview only — no writes)

Always safe to run. Shows the full diff but makes zero API calls that modify data.

```bash
python cribl-pusher.py --workspace prod --dry-run --from-file --appfile appids.txt
```

---

### Production workspace

Workspaces with `"require_allow": true` in config need an extra confirmation step. Either:

```bash
# Interactive — the script will pause and ask you to type ALLOW
python cribl-pusher.py --workspace prod --from-file --appfile appids.txt

# Non-interactive — pass the flag to skip the ALLOW prompt
python cribl-pusher.py --workspace prod --allow-prod --from-file --appfile appids.txt --yes
```

---

### Using a route group

If your Cribl routes are organised into named groups:

```bash
python cribl-pusher.py \
  --workspace dev \
  --group-id my-group-id \
  --from-file
```

If the group does not exist yet and you want to create it:

```bash
python cribl-pusher.py \
  --workspace dev \
  --group-id my-group-id \
  --create-missing-group \
  --group-name "My New Group" \
  --from-file
```

---

## All CLI Flags

| Flag | Default | Description |
|---|---|---|
| `--config` | `config.json` | Path to the config file |
| `--workspace` | *(prompts)* | Workspace name (must match a key in config `workspaces`) |
| `--allow-prod` | false | Skip the ALLOW prompt for workspaces with `require_allow: true` |
| `--token` | `""` | Bearer token override |
| `--username` | `""` | Username override |
| `--password` | `""` | Password override |
| `--skip-ssl` | false | Disable SSL verification for this run |
| `--dry-run` | false | Preview only — no API writes |
| `--yes` | false | Skip the final `YES` confirmation prompt |
| `--appid` | *(prompts)* | Single app ID |
| `--appname` | *(prompts)* | Single app name (required with `--appid`) |
| `--from-file` | false | Load apps from a file instead |
| `--appfile` | `appids.txt` | Path to the apps file |
| `--group-id` | `""` | Insert routes into this route-group ID |
| `--create-missing-group` | false | Create the group if it doesn't exist |
| `--group-name` | `""` | Display name when creating a missing group |
| `--min-existing-total-routes` | *(from config)* | Override the safety minimum route count |
| `--diff-lines` | *(from config)* | Lines of context in the diff preview |
| `--snapshot-dir` | *(from config)* | Override the snapshot directory |

---

## Safety Features

The script has several guards that prevent accidental data loss:

| Guard | What it does |
|---|---|
| **Diff preview** | Always shows a full unified diff before asking for confirmation |
| **Minimum routes check** | Refuses to PATCH if the API returns fewer routes than `min_existing_total_routes` (protects against an empty/wrong response being pushed back) |
| **No-shrink check** | Refuses to PATCH if the new total route count is less than the current count |
| **Duplicate skip** | Silently skips any app whose route name or filter already exists — never creates duplicates |
| **require_allow** | Prod workspaces require typing `ALLOW` before any writes |
| **Dry run** | `--dry-run` runs the full logic (including auth and GET) but never calls POST or PATCH |
| **Rollback snapshot** | The original route object is saved to `cribl_snapshots/{workspace}/` before every PATCH |

---

## Rolling Back a Change

If something went wrong after a successful PATCH, find the snapshot file printed in the output:

```
[SNAPSHOT] Saved rollback snapshot: cribl_snapshots/prod/routes_default_snapshot_20240315T143022Z.json
```

To restore, send that file back to Cribl:

```bash
curl -k -X PATCH \
  "https://YOUR_CRIBL_HOST:9000/api/v1/m/prod/routes/default" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d @cribl_snapshots/prod/routes_default_snapshot_20240315T143022Z.json
```

Replace `prod` with your worker group name and update the URL and token.

---

## Troubleshooting

### `Config file not found: config.json`

You have not created your config file yet. Copy the example:

```bash
copy config.example.json config.json   # Windows
cp config.example.json config.json     # Mac/Linux
```

Then fill in `base_url`, `credentials`, and `workspaces`.

---

### `[ERR] login failed: 401`

- Wrong username or password in `config.json`
- Or the Cribl instance requires a token — generate one in the Cribl UI under **Settings → API tokens** and put it in `credentials.token`

---

### `SSL: CERTIFICATE_VERIFY_FAILED`

Your Cribl instance uses a self-signed certificate. Set `skip_ssl` in config:

```json
"skip_ssl": true
```

Or pass the flag at runtime:

```bash
python cribl-pusher.py --skip-ssl --workspace dev ...
```

---

### `[SAFETY] Refusing to PATCH: total_before=0 < min=1`

The GET request returned an empty route table, which is almost certainly wrong. Check:
- Is `base_url` correct?
- Is `worker_group` correct for this workspace?
- Does your token/user have permission to read routes?

---

### `Cannot locate routes array/group in GET response`

The API response shape is unexpected. Run with `--dry-run` and add a temporary `print(current_obj)` after the GET to inspect what Cribl is returning.

---

### `[SKIP] route already exists for APP001`

Not an error. The route for that app was already present — the script skipped it to avoid duplicates.

---

### `ModuleNotFoundError: No module named 'requests'`

Install dependencies:

```bash
pip install requests urllib3
```
