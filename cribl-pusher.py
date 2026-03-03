#!/usr/bin/env python3
"""
cribl-pusher  —  Add routes + upsert destinations across Cribl workspaces.

Usage examples:
  python cribl-pusher.py                                  # fully interactive
  python cribl-pusher.py --workspace dev --appid APP1 --appname "My App"
  python cribl-pusher.py --workspace prod --allow-prod --from-file --appfile appids.txt --yes
  python cribl-pusher.py --workspace qa --dry-run --from-file
"""
import os
import json
import copy
import argparse
from pathlib import Path

from cribl_utils import (
    die, short_id, now_stamp, pretty_json, unified_diff,
    read_json, read_apps_from_file,
    prompt_choice, prompt_text, prompt_password, confirm_or_exit,
    make_session,
)
from cribl_api import (
    cribl_login_token, normalize_route, find_default_route_index,
    get_routes_target, create_group_if_missing, count_all_routes,
)
from cribl_config import (
    load_config, get_workspace_names, get_workspace,
    build_workspace_urls, resolve_credentials,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cribl: add routes + upsert destinations across configurable workspaces.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Config
    p.add_argument("--config", default="config.json",
                   help="Path to config file (default: config.json)")

    # Workspace
    p.add_argument("--workspace",
                   help="Workspace name from config (if omitted, prompts interactively)")
    p.add_argument("--allow-prod", action="store_true",
                   help="Required for workspaces marked require_allow=true")

    # Auth overrides — lowest priority; config.json and env vars are checked first
    p.add_argument("--token", default="", help="Bearer token override (overrides config + env)")
    p.add_argument("--username", default="", help="Username override (overrides config + env)")
    p.add_argument("--password", default="", help="Password override (overrides config + env)")

    # SSL / execution
    p.add_argument("--skip-ssl", action="store_true",
                   help="Skip SSL verification (overrides config)")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview changes only — no API writes")
    p.add_argument("--yes", action="store_true",
                   help="Non-interactive: skip YES confirmation prompt")

    # App selection
    p.add_argument("--appid", help="Single app ID")
    p.add_argument("--appname", help="Single app name (required with --appid)")
    p.add_argument("--from-file", action="store_true",
                   help="Bulk mode: load apps from file")
    p.add_argument("--appfile", default="appids.txt",
                   help="Apps file path (default: appids.txt)")

    # Route group
    p.add_argument("--group-id", default="",
                   help="Insert routes into this route-group ID")
    p.add_argument("--create-missing-group", action="store_true",
                   help="Create the route group if it does not exist")
    p.add_argument("--group-name", default="",
                   help="Display name when creating a missing group")

    # Safety overrides (all have config.json defaults)
    p.add_argument("--min-existing-total-routes", type=int, default=None,
                   help="Override config min_existing_total_routes")
    p.add_argument("--diff-lines", type=int, default=None,
                   help="Override config diff_lines")
    p.add_argument("--snapshot-dir", default="",
                   help="Override config snapshot_dir")

    return p


def main():
    args = build_parser().parse_args()

    # ── Config ────────────────────────────────────────────────────────────────
    config = load_config(args.config)
    workspace_names = get_workspace_names(config)
    if not workspace_names:
        die("[ERR] No workspaces defined in config.json")

    # ── Workspace selection ────────────────────────────────────────────────────
    if not args.workspace:
        args.workspace = prompt_choice("Select workspace", workspace_names)

    workspace_cfg = get_workspace(config, args.workspace)

    if workspace_cfg.get("require_allow") and not args.allow_prod:
        print(f"\n[WARN] Workspace '{args.workspace}' requires explicit confirmation.")
        answer = prompt_text('Type "ALLOW" to proceed (anything else aborts)', "")
        if answer.strip() != "ALLOW":
            die("Refusing to run: ALLOW not confirmed.")
        args.allow_prod = True

    # ── App selection ─────────────────────────────────────────────────────────
    if args.appid:
        if not args.appname:
            args.appname = prompt_text("appname")
    else:
        if not args.from_file:
            mode = prompt_choice("Mode", ["single", "file"])
            if mode == "single":
                args.appid = prompt_text("appid")
                args.appname = prompt_text("appname")
            else:
                args.from_file = True

        if args.from_file and not args.appid and not os.path.exists(args.appfile):
            args.appfile = prompt_text("appfile", args.appfile)

    # ── Credentials ───────────────────────────────────────────────────────────
    token, username, password = resolve_credentials(config, args)

    if not token:
        if not username:
            username = prompt_text("Username")
        if not password:
            password = prompt_password()

    # ── Load apps ─────────────────────────────────────────────────────────────
    if args.appid:
        apps = [(args.appid.strip(), (args.appname or "").strip())]
        if not apps[0][1]:
            die("appname is required.")
        mode_desc = "single"
    else:
        if not args.from_file:
            die("Refusing to run: choose --appid/--appname or --from-file.")
        apps = read_apps_from_file(args.appfile)
        if not apps:
            die(f"App file is empty: {args.appfile}")
        mode_desc = f"bulk({len(apps)})"

    # ── Resolve settings (CLI > workspace > global config) ───────────────────
    # skip_ssl: --skip-ssl flag wins, then workspace-level, then global config
    skip_ssl   = args.skip_ssl or workspace_cfg.get("skip_ssl", config.get("skip_ssl", False))
    min_routes = (args.min_existing_total_routes
                  if args.min_existing_total_routes is not None
                  else config.get("min_existing_total_routes", 1))
    diff_lines = (args.diff_lines
                  if args.diff_lines is not None
                  else config.get("diff_lines", 3))
    snapshot_dir = args.snapshot_dir or config.get("snapshot_dir", "cribl_snapshots")

    # ── URLs ──────────────────────────────────────────────────────────────────
    root_url, api_base = build_workspace_urls(config, workspace_cfg)

    # ── Templates ─────────────────────────────────────────────────────────────
    route_tmpl_path = (workspace_cfg.get("route_template")
                       or config.get("route_template", "route_template.json"))
    dest_tmpl_path  = workspace_cfg.get("dest_template", "")
    if not dest_tmpl_path:
        die(f"[ERR] No dest_template defined for workspace '{args.workspace}'")

    route_template = read_json(route_tmpl_path)
    dest_template  = read_json(dest_tmpl_path)
    fallback_pipeline = route_template.get("pipeline") or "passthru"

    # ── Session + auth ────────────────────────────────────────────────────────
    session = make_session(skip_ssl)
    if not token:
        token = cribl_login_token(session, root_url, username, password)

    def H():
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def GET(url):
        return session.get(url, headers=H(), timeout=60)

    def POST(url, payload):
        return session.post(url, headers=H(), json=payload, timeout=60)

    def PATCH(url, payload):
        return session.patch(url, headers=H(), json=payload, timeout=60)

    outputs_url = f"{api_base}/system/outputs"
    routes_url  = f"{api_base}/routes/default"
    group_id    = (args.group_id or "").strip() or None

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n=== TARGET ===")
    print(f"workspace    : {args.workspace}  ({workspace_cfg.get('description', '')})")
    print(f"worker_group : {workspace_cfg['worker_group']}")
    print(f"api_base     : {api_base}")
    print(f"mode         : {mode_desc}")
    print(f"apps         : {len(apps)}")
    print(f"group-id     : {group_id or '(none)'}")
    print(f"dry-run      : {args.dry_run}")
    print(f"skip-ssl     : {skip_ssl}")

    # ── 1) GET current routes ─────────────────────────────────────────────────
    rget = GET(routes_url)
    if rget.status_code != 200:
        die(f"[ERR] GET routes/default: {rget.status_code} {rget.text}")

    current_obj  = rget.json()
    total_before = count_all_routes(current_obj)
    print(f"[INFO] Loaded total routes (all groups): {total_before}")

    if total_before < min_routes:
        die(f"[SAFETY] Refusing to PATCH: total_before={total_before} < min={min_routes}")

    # ── Ensure group exists if requested ──────────────────────────────────────
    if group_id:
        tgt, tgt_key, _ = get_routes_target(current_obj, group_id)
        if tgt is None:
            if not args.create_missing_group:
                die(
                    f"[SAFETY] group-id '{group_id}' not found. "
                    f"Use --create-missing-group to create it."
                )
            create_group_if_missing(current_obj, group_id, args.group_name.strip() or None)
            tgt, tgt_key, _ = get_routes_target(current_obj, group_id)
            if tgt is None:
                die(f"[ERR] Failed to create/locate group '{group_id}' after creation")

    # ── 2) Build the patched object ───────────────────────────────────────────
    patch_obj = copy.deepcopy(current_obj)
    target_container, routes_key, _ = get_routes_target(patch_obj, group_id)
    routes_list_raw = target_container.get(routes_key)
    if not isinstance(routes_list_raw, list):
        die("[ERR] Target routes list is not a list (unexpected API shape)")

    existing_routes  = [normalize_route(copy.deepcopy(r), fallback_pipeline) for r in routes_list_raw]
    default_idx      = find_default_route_index(existing_routes)
    existing_names   = {r.get("name") for r in existing_routes if isinstance(r, dict) and r.get("name")}
    existing_filters = {r.get("filter") for r in existing_routes if isinstance(r, dict) and r.get("filter")}

    new_routes = []
    for appid, appname in apps:
        route           = copy.deepcopy(route_template)
        route["id"]     = short_id("route")
        route["filter"] = f'apmId == "{appid}"'
        route["output"] = f"hcsc-blob-storage-northcentralus-{appid}"
        route["name"]   = f"hcsc-blob-storage-route-{appid}"
        route           = normalize_route(route, fallback_pipeline)

        if route["name"] in existing_names or route["filter"] in existing_filters:
            print(f"[SKIP] route already exists for {appid}")
            continue

        new_routes.append(route)
        existing_names.add(route["name"])
        existing_filters.add(route["filter"])

    updated_routes = existing_routes[:default_idx] + new_routes + existing_routes[default_idx:]
    target_container[routes_key] = updated_routes

    # ── 3) Preview diff ───────────────────────────────────────────────────────
    before_text = pretty_json(current_obj)
    after_text  = pretty_json(patch_obj)
    diff        = unified_diff(
        before_text, after_text,
        "routes_default_before.json", "routes_default_after.json",
        n=diff_lines,
    )
    total_after = count_all_routes(patch_obj)

    print("\n=== ROUTE PLAN ===")
    print(f"target scope      : {'group:' + group_id if group_id else 'top-level routes'}")
    print(f"existing in scope : {len(existing_routes)}")
    print(f"new routes        : {len(new_routes)}")
    print(f"final in scope    : {len(updated_routes)}")
    print(f"total routes all  : {total_before} -> {total_after}")

    if total_after < total_before:
        die(f"[SAFETY] Refusing to PATCH: total_after ({total_after}) < total_before ({total_before})")

    if diff.strip():
        print("\n--- FULL OBJECT DIFF (preview) ---")
        print(diff)
    else:
        print("[INFO] No route changes detected.")

    # ── 4) Confirmation ───────────────────────────────────────────────────────
    confirm_or_exit("\nProceed to APPLY these changes?", args.yes)

    if args.dry_run:
        print("\n[DRY RUN] No API writes performed.")
        return

    # ── 5) Snapshot for rollback ──────────────────────────────────────────────
    snap_dir  = Path(snapshot_dir) / args.workspace
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / f"routes_default_snapshot_{now_stamp()}.json"
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(current_obj, f, indent=2)
    print(f"[SNAPSHOT] Saved rollback snapshot: {snap_file}")

    # ── 6) Upsert destinations ────────────────────────────────────────────────
    for appid, appname in apps:
        dest                  = copy.deepcopy(dest_template)
        dest_id               = f"hcsc-blob-storage-northcentralus-{appid}"
        dest["id"]            = dest_id
        dest["containerName"] = f'"{appid}"'
        dest["description"]   = appname
        if "name" in dest:
            dest["name"] = dest_id

        rp = POST(outputs_url, dest)
        if rp.status_code in (200, 201):
            print(f"[OK] Created destination {dest_id}")
        elif rp.status_code in (400, 409):
            rpu = PATCH(f"{outputs_url}/{dest_id}", dest)
            if rpu.status_code in (200, 204):
                print(f"[OK] Updated destination {dest_id}")
            else:
                die(f"[ERR] Update destination {dest_id}: {rpu.status_code} {rpu.text}")
        else:
            die(f"[ERR] Create destination {dest_id}: {rp.status_code} {rp.text}")

    # ── 7) PATCH routes/default ───────────────────────────────────────────────
    rpatch = PATCH(routes_url, patch_obj)
    if rpatch.status_code in (200, 204):
        print(f"[OK] PATCH routes/default — added {len(new_routes)} new routes.")
        print(f"[ROLLBACK] Restore snapshot: {snap_file}")
    else:
        die(f"[ERR] PATCH routes/default: {rpatch.status_code} {rpatch.text}")


if __name__ == "__main__":
    main()
