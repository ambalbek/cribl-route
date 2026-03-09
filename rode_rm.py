import os, json, copy, getpass, logging, sys
from jinja2 import Template
import argparse
from pathlib import Path

from cribl_utils import (
    die, now_stamp, pretty_json, unified_diff,
    read_json, make_session, confirm_or_exit,
    prompt_text, prompt_password, prompt_choice,
)
from cribl_api import (
    cribl_login_token, normalize_route, find_default_route_index,
    get_routes_target, create_group_if_missing, count_all_routes,
    unwrap_response,
)
from cribl_config import (
    load_config, get_workspace_names, get_workspace,
    build_workspace_urls, resolve_credentials,
)

"""
This how to use below script, all you need is from command line execute, dont forget to put arguments.
    required packages:
        - jinja2
        - argparse
        - requests
    Example usage:
        # Generate templates only (no API calls):
        ./rode_rm.py --app_name=APP00000001-RESTAIL-STATUS-TRACKING-SYS --apmid=app00000001 --skip-elk --skip-cribl

        # ELK + Cribl (default order: elk first):
        ./rode_rm.py --app_name=APP00000001-RESTAIL-STATUS-TRACKING-SYS --apmid=app00000001 \
            --elk-url=https://elk.example.com:9200 --elk-user=elastic --workspace=dev

        # Cribl first, then ELK:
        ./rode_rm.py --app_name=APP00000001-RESTAIL --apmid=app00000001 \
            --elk-url=https://elk.example.com:9200 --elk-user=elastic --workspace=prod --allow-prod \
            --order=cribl-first --dry-run
"""


# ── your original templates (unchanged) ──────────────────────────────────────

role_template = '''
POST /_security/role/R-{{APP_NAME}}-{{ENVIRONMENT}}-{{REGION}}-{{USER_TYPE}}
{
  "cluster": [
    {{cluster_privileges | join(',\n    ')}}
  ],
  "indices": [
    {
      "names": [
        "logs-kubernetes.container_logs-*",
        "logs-kubernetes.container_logs-arot*",
        "logs-kubernetes.container_logs-arost*",
        "logs-kubernetes.container_logs-arod*",
        "logs-kubernetes.container_logs-ocpwt*",
        "logs-kubernetes.container_logs-arop*",
        "logs-kubernetes.container_logs-arosp*",
        "logs-kubernetes.container_logs-ocpznp*",
        "logs-kubernetes.container_logs-ocpzsp*",
        "logs-kubernetes.container_logs-ocpwp*",
        "partial*",
        "restored*"
      ],
      "privileges": [
        "read",
        "read_cross_cluster",
        "monitor",
        "view_index_metadata"
      ],
      "field_security": {
        "grant": ["*"]
      },
      "query": """{"template":{"source":{"query_string":{"query":"{{apmid}}","default_field":"apmId"}}}}""",
      "allow_restricted_indices": false
    }
  ],
  "applications": [
    {
      "application": "kibana-.kibana",
      "privileges": [
        "read",
        "view_index_metadata",
        "read_cross_cluster"
      ],
      "resources": ["*"]
    },
    {
      "application": "kibana-.kibana",
      "privileges": ["space_all"],
      "resources": ["space:default"]
    }
  ],
  "run_as": [],
  "metadata": {},
  "transient_metadata": {
    "enabled": true
  }
}
'''

role_mapping_template = '''
POST /_security/role_mapping/RM-{{APP_NAME}}-{{ENVIRONMENT}}-{{REGION}}-{{USER_TYPE}}
{
  "enabled": true,
  "roles": [
    "R-{{APP_NAME}}-{{ENVIRONMENT}}-{{REGION}}-{{USER_TYPE}}",
    "monitoring_user",
    "transport_client",
    {{additional_roles | join(',\n    ')}}
  ],
  "rules": {
    "field": {
      "groups": "CN=ELK_{{APP_NAME}}_{{ENVIRONMENT}}_{{REGION}}_{{USER_TYPE}},OU=Groups,OU=Global,OU=HCSC,DC={{DOMAIN}},DC=net"
    }
  },
  "metadata": {}
}
'''


# ── your original generate_templates (unchanged) ──────────────────────────────

def generate_templates(app_name, apmid, environment, region, user_type, domain, additional_roles):
    cluster_privileges = [
        '"monitor"',
        '"manage_watcher"' if user_type == 'PUSER' else '"monitor_watcher"',
        '"transport_client"',
        '"cross_cluster_search"'
    ]
    if user_type.upper() == 'PUSER':
        cluster_privileges.append('"monitor_ml"')
        cluster_privileges.append('"monitor_rollup"')

    role_rendered = Template(role_template).render(
        APP_NAME=app_name,
        apmid=apmid,
        REGION=region.upper(),
        ENVIRONMENT=environment.upper(),
        USER_TYPE=user_type.upper(),
        cluster_privileges=cluster_privileges
    )

    role_mapping_rendered = Template(role_mapping_template).render(
        APP_NAME=app_name,
        REGION=region.upper(),
        USER_TYPE=user_type.upper(),
        ENVIRONMENT=environment.upper(),
        DOMAIN=domain,
        additional_roles=[f'"{role}"' for role in additional_roles]
    )

    return role_rendered, role_mapping_rendered


# ── ELK push: parses rendered Kibana console output and calls the API ─────────

def _parse_kibana_console(rendered: str):
    """
    Parse a Kibana Dev Console block like:
        POST /_security/role/R-APP-TEST-ONSHORE-PUSER
        { ... json ... }
    Returns (method, path, body_dict).
    """
    lines = rendered.strip().splitlines()
    first = lines[0].strip()
    method, path = first.split(" ", 1)
    body = json.loads("\n".join(lines[1:]))
    return method, path, body


def push_elk(app_name, apmid, configurations, elk_url, session, headers, dry_run, log):
    base = elk_url.rstrip("/")
    ok = True

    all_roles         = []
    all_role_mappings = []

    for cfg in configurations:
        region      = cfg["region"]
        environment = cfg["environment"]
        domain      = cfg["domain"]
        roles       = cfg["roles"]

        role_puser,    rm_puser = generate_templates(app_name, apmid, environment, region, "PUSER", domain, roles)
        role_user,     rm_user  = generate_templates(app_name, apmid, environment, region, "USER",  domain, roles)

        all_roles         += [role_puser, role_user]
        all_role_mappings += [rm_puser,   rm_user]

    for rendered in all_roles + all_role_mappings:
        _, path, body = _parse_kibana_console(rendered)
        url = base + path
        if dry_run:
            log.info(f"  [DRY-RUN] PUT {url}")
            continue
        log.debug(f"  PUT {url}")
        r = session.put(url, headers=headers, json=body, timeout=60)
        if r.status_code in (200, 201):
            log.info(f"  [OK]  {url}")
        else:
            log.error(f"  [ERR] {url} → {r.status_code}: {r.text}")
            ok = False

    return ok


# ── save templates to disk (your original logic, unchanged) ───────────────────

def save_templates(app_name, apmid, configurations):
    os.makedirs("ops_rm_r_templates_output", exist_ok=True)

    all_roles         = []
    all_role_mappings = []

    for cfg in configurations:
        region      = cfg["region"]
        environment = cfg["environment"]
        domain      = cfg["domain"]
        roles       = cfg["roles"]

        role_puser, role_mapping_puser = generate_templates(app_name, apmid, environment, region, "PUSER", domain, roles)
        role_user,  role_mapping_user  = generate_templates(app_name, apmid, environment, region, "USER",  domain, roles)

        all_roles.append(role_puser)
        all_roles.append(role_user)
        all_role_mappings.append(role_mapping_puser)
        all_role_mappings.append(role_mapping_user)

    with open(f"ops_rm_r_templates_output/roles_{apmid}.json", "w") as f:
        f.write("\n".join(all_roles))

    with open(f"ops_rm_r_templates_output/role_mappings_{apmid}.json", "w") as f:
        f.write("\n".join(all_role_mappings))

    print("Consolidated templates generated and saved in 'ops_rm_r_templates_output/' directory.")


# ── Cribl push (framework pattern) ───────────────────────────────────────────

def push_cribl(appid, appname, workspace_name, args, log):
    config        = load_config(args.config)
    workspace_cfg = get_workspace(config, workspace_name)

    if workspace_cfg.get("require_allow") and not args.allow_prod:
        log.warning(f"Workspace '{workspace_name}' requires explicit confirmation.")
        answer = input('Type "ALLOW" to proceed (anything else aborts): ').strip()
        if answer != "ALLOW":
            die("Aborted — ALLOW not confirmed.")
        args.allow_prod = True

    skip_ssl     = args.skip_ssl or workspace_cfg.get("skip_ssl", config.get("skip_ssl", False))
    min_routes   = config.get("min_existing_total_routes", 1)
    diff_lines   = config.get("diff_lines", 3)
    snapshot_dir = config.get("snapshot_dir", "cribl_snapshots")

    root_url, api_base = build_workspace_urls(config, workspace_cfg)

    route_tmpl_path = workspace_cfg.get("route_template") or config.get("route_template", "route_template.json")
    dest_tmpl_path  = workspace_cfg.get("dest_template", "")
    if not dest_tmpl_path:
        die(f"[ERR] No dest_template defined for workspace '{workspace_name}'")

    route_template    = read_json(route_tmpl_path)
    dest_template     = read_json(dest_tmpl_path)
    fallback_pipeline = route_template.get("pipeline") or "passthru"

    token, username, password = resolve_credentials(config, args)
    session = make_session(skip_ssl)
    if not token:
        if not username:
            username = prompt_text("Cribl username")
        if not password:
            password = prompt_password("Cribl password")
        token = cribl_login_token(session, root_url, username, password)

    def H():
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    routes_table = workspace_cfg.get("routes_table", workspace_cfg["worker_group"])
    routes_url   = f"{api_base}/routes/{routes_table}"
    outputs_url  = f"{api_base}/system/outputs"

    rget = session.get(routes_url, headers=H(), timeout=60)
    if rget.status_code != 200:
        die(f"[ERR] GET routes: {rget.status_code} {rget.text}")

    current_obj  = rget.json()
    total_before = count_all_routes(current_obj)
    log.info(f"  Existing routes: {total_before}")

    if total_before < min_routes:
        die(f"[SAFETY] total_before={total_before} < min={min_routes}")

    dget = session.get(outputs_url, headers=H(), timeout=60)
    if dget.status_code != 200:
        die(f"[ERR] GET outputs: {dget.status_code} {dget.text}")

    existing_dest_ids = {
        item["id"]
        for item in dget.json().get("items", [])
        if isinstance(item, dict) and item.get("id")
    }

    patch_obj = copy.deepcopy(current_obj)
    target_container, routes_key, _ = get_routes_target(patch_obj, None)
    routes_list_raw = target_container.get(routes_key)
    if not isinstance(routes_list_raw, list):
        die("[ERR] Routes list is not a list (unexpected API shape)")

    existing_routes  = [r for r in routes_list_raw if isinstance(r, dict) and r.get("filter") is not None]
    existing_names   = {r.get("name")   for r in existing_routes if r.get("name")}
    existing_filters = {r.get("filter") for r in existing_routes if r.get("filter")}
    default_idx      = find_default_route_index(existing_routes)

    route = copy.deepcopy(route_template)
    route["id"]     = appid
    route["filter"] = f'apmId == "{appid}"'
    route["output"] = f"hcsc-blob-storage-northcentralus-{appid}"
    route["name"]   = f"hcsc-blob-storage-route-{appid}"
    route           = normalize_route(route, fallback_pipeline)

    if route["name"] in existing_names or route["filter"] in existing_filters:
        log.info(f"  [SKIP] Cribl route already exists for {appid}")
        new_routes = []
    else:
        new_routes = [route]

    updated_routes = existing_routes[:default_idx] + new_routes + existing_routes[default_idx:]
    target_container[routes_key] = updated_routes

    diff = unified_diff(
        pretty_json(unwrap_response(current_obj)),
        pretty_json(unwrap_response(patch_obj)),
        "routes_before.json", "routes_after.json",
        n=diff_lines,
    )
    total_after = count_all_routes(patch_obj)
    log.info(f"  Route plan: {len(existing_routes)} existing + {len(new_routes)} new = {len(updated_routes)}")
    log.info(f"  Grand total: {total_before} -> {total_after}")
    if diff.strip():
        for line in diff.splitlines():
            log.info(f"  {line}")

    if total_after < total_before:
        die(f"[SAFETY] total_after ({total_after}) < total_before ({total_before})")

    if args.dry_run:
        log.info("  [DRY-RUN] Cribl writes skipped.")
        return True

    snap_dir  = Path(snapshot_dir) / workspace_name
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / f"routes_snapshot_{now_stamp()}.json"
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(current_obj, f, indent=2)
    log.info(f"  [SNAPSHOT] {snap_file}")

    dest_id = f"hcsc-blob-storage-northcentralus-{appid}"
    if dest_id in existing_dest_ids:
        log.info(f"  [SKIP] Destination already exists: {dest_id}")
    else:
        dest = copy.deepcopy(dest_template)
        dest["id"]            = dest_id
        dest["containerName"] = f'"{appid}"'
        dest["description"]   = appname
        if "name" in dest:
            dest["name"] = dest_id
        rp = session.post(outputs_url, headers=H(), json=dest, timeout=60)
        if rp.status_code in (200, 201):
            log.info(f"  [OK] Created destination {dest_id}")
        else:
            die(f"[ERR] Create destination {dest_id}: {rp.status_code} {rp.text}")

    if new_routes:
        rpatch = session.patch(routes_url, headers=H(), json=unwrap_response(patch_obj), timeout=60)
        if rpatch.status_code in (200, 204):
            log.info(f"  [OK] PATCH routes — added {len(new_routes)} new route(s).")
            log.info(f"  [ROLLBACK] Restore: {snap_file}")
        else:
            die(f"[ERR] PATCH routes: {rpatch.status_code} {rpatch.text}")
    else:
        log.info("  No route changes to PATCH.")

    return True


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate role and role mapping templates for Elasticsearch, push to ELK, and apply Cribl routes.")
    # original args
    parser.add_argument("--app_name", type=str, required=True,  help="The application name.")
    parser.add_argument("--apmid",    type=str, required=True,  help="The lower-cased organization name.")
    # ELK push args
    parser.add_argument("--elk-url",      default="", help="Elasticsearch base URL (required unless --skip-elk)")
    parser.add_argument("--elk-user",     default="", help="Elasticsearch username (basic auth)")
    parser.add_argument("--elk-password", default="", help="Elasticsearch password (prompted if blank)")
    parser.add_argument("--elk-token",    default="", help="Elasticsearch API key (overrides basic auth)")
    parser.add_argument("--skip-elk",     action="store_true", help="Skip ELK API calls (templates still saved)")
    # Cribl args
    parser.add_argument("--config",     default="config.json", help="Path to Cribl config file")
    parser.add_argument("--workspace",  default="",            help="Cribl workspace name")
    parser.add_argument("--allow-prod", action="store_true",   help="Required for require_allow workspaces")
    parser.add_argument("--token",      default="",            help="Cribl bearer token override")
    parser.add_argument("--username",   default="",            help="Cribl username override")
    parser.add_argument("--password",   default="",            help="Cribl password override")
    parser.add_argument("--skip-cribl", action="store_true",   help="Skip Cribl route/destination push")
    # shared
    parser.add_argument("--order",    choices=["elk-first", "cribl-first"], default="elk-first",
                        help="Which side to run first (default: elk-first)")
    parser.add_argument("--skip-ssl", action="store_true", help="Disable SSL verification")
    parser.add_argument("--dry-run",  action="store_true", help="Preview only — no API writes")
    parser.add_argument("--yes",      action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    # logging
    log = logging.getLogger("rode_rm")
    log.setLevel(getattr(logging, args.log_level))
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S"))
        log.addHandler(h)
    log.propagate = False

    app_name = args.app_name
    apmid    = args.apmid

    configurations = [
        {"region": "onshore",  "environment": "test",  "domain": "adhcsctst", "roles": ["watcher_user"]},
        {"region": "offshore", "environment": "test",  "domain": "adhcsctst", "roles": ["watcher_user"]},
        {"region": "onshore",  "environment": "prod",  "domain": "adhcscint", "roles": ["watcher_admin"]},
        {"region": "offshore", "environment": "prod",  "domain": "adhcscint", "roles": ["watcher_admin"]}
    ]

    # validate
    if not args.skip_elk and not args.elk_url:
        die("--elk-url is required unless --skip-elk is set.")
    if not args.skip_elk and not args.elk_token and not args.elk_user:
        die("Provide --elk-user or --elk-token unless --skip-elk is set.")
    if not args.skip_elk and not args.elk_token and not args.elk_password:
        args.elk_password = getpass.getpass("Elasticsearch password: ")
    if not args.skip_cribl and not args.workspace:
        config_data = load_config(args.config)
        ws_names    = get_workspace_names(config_data)
        if not ws_names:
            die("No workspaces defined in config.json")
        args.workspace = prompt_choice("Select Cribl workspace", ws_names)

    # always save templates to disk (original behaviour)
    save_templates(app_name, apmid, configurations)

    confirm_or_exit("\nProceed to push to ELK and Cribl?", args.yes or args.dry_run)

    # build ELK session/headers once
    import base64, urllib3
    if args.skip_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    import requests
    elk_session = requests.Session()
    elk_session.verify = not args.skip_ssl
    if args.elk_token:
        elk_headers = {"Content-Type": "application/json", "Accept": "application/json",
                       "Authorization": f"ApiKey {args.elk_token}"}
    else:
        creds = base64.b64encode(f"{args.elk_user}:{args.elk_password}".encode()).decode()
        elk_headers = {"Content-Type": "application/json", "Accept": "application/json",
                       "Authorization": f"Basic {creds}"}

    def run_elk():
        if args.skip_elk:
            log.info("[ELK] Skipped.")
            return
        log.info("[ELK] Pushing roles and role-mappings …")
        ok = push_elk(app_name, apmid, configurations,
                      args.elk_url, elk_session, elk_headers, args.dry_run, log)
        if ok:
            log.info("[ELK] Done.")
        else:
            die("[ELK] One or more writes failed.")

    def run_cribl():
        if args.skip_cribl:
            log.info("[CRIBL] Skipped.")
            return
        log.info(f"[CRIBL] Pushing route + destination to workspace '{args.workspace}' …")
        push_cribl(apmid, app_name, args.workspace, args, log)
        log.info("[CRIBL] Done.")

    if args.order == "elk-first":
        run_elk()
        run_cribl()
    else:
        run_cribl()
        run_elk()


if __name__ == "__main__":
    main()
