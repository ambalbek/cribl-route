#!/usr/bin/env python3
"""
ui.py — Streamlit GUI wrapper for cribl-pusher.py and rode_rm.py

Run with:
    streamlit run ui.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st

# ── Constants ─────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
PUSHER      = SCRIPT_DIR / "cribl-pusher.py"
RODE_RM     = SCRIPT_DIR / "rode_rm.py"
LOG_LEVELS  = ["INFO", "DEBUG", "WARNING", "ERROR"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_config_safe() -> dict | None:
    if not CONFIG_PATH.exists():
        st.error(
            f"config.json not found at {CONFIG_PATH}. "
            "Copy config.example.json to config.json and fill in your values."
        )
        return None
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        st.error(f"config.json is invalid JSON: {exc}")
        return None


def ws_label(name: str, cfg: dict) -> str:
    desc = cfg.get("description", "")
    return f"{name}  —  {desc}" if desc else name


def build_command(
    workspace, allow_prod, cribl_url, mode, appid, appname, appfile_path,
    dry_run, skip_ssl, log_level, log_file,
    token, username, password,
    group_id, create_missing_group, group_name,
    min_routes, diff_lines, snapshot_dir,
) -> list[str]:
    cmd = [
        sys.executable, str(PUSHER),
        "--yes",
        "--workspace", workspace,
        "--log-level", log_level,
        "--config", str(CONFIG_PATH),
    ]

    if cribl_url.strip():   cmd += ["--cribl-url", cribl_url.strip()]
    if allow_prod:          cmd.append("--allow-prod")
    if dry_run:             cmd.append("--dry-run")
    if skip_ssl:            cmd.append("--skip-ssl")

    if token.strip():
        cmd += ["--token", token.strip()]
    elif username.strip() and password.strip():
        cmd += ["--username", username.strip(), "--password", password.strip()]

    if mode == "single":
        cmd += ["--appid", appid.strip(), "--appname", appname.strip()]
    else:
        cmd += ["--from-file", "--appfile", appfile_path]

    if group_id.strip():
        cmd += ["--group-id", group_id.strip()]
        if create_missing_group:
            cmd.append("--create-missing-group")
        if group_name.strip():
            cmd += ["--group-name", group_name.strip()]

    if min_routes.strip():  cmd += ["--min-existing-total-routes", min_routes.strip()]
    if diff_lines.strip():  cmd += ["--diff-lines", diff_lines.strip()]
    if snapshot_dir.strip():cmd += ["--snapshot-dir", snapshot_dir.strip()]
    if log_file.strip():    cmd += ["--log-file", log_file.strip()]

    return cmd


def validate(mode, appid, appname, uploaded_file,
             token, username, password, min_routes, diff_lines) -> list[str]:
    errors = []
    if mode == "single":
        if not appid.strip():   errors.append("App ID is required.")
        if not appname.strip(): errors.append("App Name is required.")
    else:
        if uploaded_file is None:
            errors.append("Please upload an app list file (.txt).")

    has_token = bool(token.strip())
    has_user  = bool(username.strip())
    has_pass  = bool(password.strip())
    if not has_token:
        if has_user and not has_pass: errors.append("Password is required when Username is set.")
        if has_pass and not has_user: errors.append("Username is required when Password is set.")

    for label, val in [("Min Existing Total Routes", min_routes), ("Diff Context Lines", diff_lines)]:
        if val.strip():
            try:    int(val.strip())
            except ValueError: errors.append(f"{label} must be an integer.")

    return errors


def run_subprocess(cmd: list[str]) -> tuple[str, int]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(SCRIPT_DIR),
    )
    return result.stdout or "", result.returncode


def build_command_rm(
    mode, app_name, apmid, appfile_path,
    elk_url, elk_user, elk_password, elk_token,
    cribl_url, workspace, allow_prod, order,
    skip_elk, skip_cribl,
    dry_run, skip_ssl, log_level,
) -> list[str]:
    cmd = [sys.executable, str(RODE_RM), "--yes"]

    if mode == "single":
        cmd += ["--app_name", app_name.strip(), "--apmid", apmid.strip()]
    else:
        cmd += ["--from-file", "--appfile", appfile_path]

    if not skip_elk:
        cmd += ["--elk-url", elk_url.strip()]
        if elk_token.strip():
            cmd += ["--elk-token", elk_token.strip()]
        elif elk_user.strip():
            cmd += ["--elk-user", elk_user.strip()]
            if elk_password.strip():
                cmd += ["--elk-password", elk_password.strip()]

    if cribl_url.strip():
        cmd += ["--cribl-url", cribl_url.strip()]
    cmd += ["--workspace", workspace]
    if allow_prod:   cmd.append("--allow-prod")
    cmd += ["--order", order]
    if skip_elk:     cmd.append("--skip-elk")
    if skip_cribl:   cmd.append("--skip-cribl")
    if dry_run:      cmd.append("--dry-run")
    if skip_ssl:     cmd.append("--skip-ssl")
    cmd += ["--log-level", log_level]

    return cmd


def validate_rm(
    mode, app_name, apmid, uploaded_file,
    elk_url, elk_user, elk_password, elk_token,
    skip_elk, skip_cribl,
) -> list[str]:
    errors = []
    if mode == "single":
        if not app_name.strip(): errors.append("App Name is required.")
        if not apmid.strip():    errors.append("App ID is required.")
    else:
        if uploaded_file is None:
            errors.append("Please upload an app list file (.txt).")

    if skip_elk and skip_cribl:
        errors.append("Nothing to do: both Skip ELK and Skip Cribl are checked.")
        return errors

    if not skip_elk:
        if not elk_url.strip():
            errors.append("ELK URL is required when not skipping ELK.")
        if not elk_token.strip() and not elk_user.strip():
            errors.append("ELK User or ELK Token is required when not skipping ELK.")
        if not elk_token.strip() and elk_user.strip() and not elk_password.strip():
            errors.append("ELK Password is required when ELK User is set.")

    return errors


# ── Page setup ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Cribl Pusher",
    page_icon=":satellite_antenna:",
    layout="wide",
)

st.title("Application onboarding")
st.caption("Add routes and destinations across Cribl workspaces.")

# ── Load config ───────────────────────────────────────────────────────────────
config = load_config_safe()
if config is None:
    st.stop()

workspaces = {k: v for k, v in config.get("workspaces", {}).items() if not k.startswith("_")}
if not workspaces:
    st.error("No workspaces defined in config.json.")
    st.stop()

ws_names = list(workspaces.keys())

# ── Session state ─────────────────────────────────────────────────────────────
if "last_output"        not in st.session_state: st.session_state.last_output        = ""
if "last_returncode"    not in st.session_state: st.session_state.last_returncode    = None
if "rm_last_output"     not in st.session_state: st.session_state.rm_last_output     = ""
if "rm_last_returncode" not in st.session_state: st.session_state.rm_last_returncode = None

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["Cribl Pusher", "ELK Roles + Cribl"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Cribl Pusher (existing)
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    left, right = st.columns([1, 1], gap="large")

    # ── LEFT — Form ───────────────────────────────────────────────────────────
    with left:
        st.subheader("Workspace")

        _cribl_urls = config.get("cribl_urls", [])
        if _cribl_urls:
            cribl_url = st.selectbox("Cribl URL", _cribl_urls, key="cribl_url")
        else:
            cribl_url = st.text_input("Cribl URL", placeholder="leave blank to use config.json", key="cribl_url")

        labels         = [ws_label(n, workspaces[n]) for n in ws_names]
        selected_label = st.selectbox("Select workspace", labels)
        selected_ws    = ws_names[labels.index(selected_label)]
        ws_cfg         = workspaces[selected_ws]
        requires_allow = ws_cfg.get("require_allow", False)

        if requires_allow:
            st.warning(f"**{selected_ws}** is a protected workspace.")
            allow_prod = st.checkbox("Allow production writes (required for this workspace)")
        else:
            allow_prod = False

        st.divider()

        # ── App selection ──────────────────────────────────────────────────────
        st.subheader("App Input")
        mode = st.radio("Mode", ["Single App", "Bulk File"], horizontal=True)

        appid = appname = ""
        uploaded_file   = None

        if mode == "Single App":
            appid   = st.text_input("App ID",   placeholder="APP001")
            appname = st.text_input("App Name", placeholder="My Application")
        else:
            st.caption("One entry per line: `appid, appname` — lines starting with `#` are ignored.")
            uploaded_file = st.file_uploader("App list (.txt)", type=["txt"])
            if uploaded_file:
                raw   = uploaded_file.getvalue().decode("utf-8", errors="replace")
                valid = [l for l in raw.splitlines() if l.strip() and not l.strip().startswith("#")]
                st.caption(f"{len(valid)} app(s) found in file.")
                with st.expander("Preview first 5"):
                    st.code("\n".join(valid[:5]) or "(empty)")
                uploaded_file.seek(0)

        st.divider()

        # ── Options ────────────────────────────────────────────────────────────
        st.subheader("Options")
        c1, c2 = st.columns(2)
        with c1:
            dry_run  = st.checkbox("Dry Run (no writes)", value=True)
            skip_ssl = st.checkbox("Skip SSL Verification")
        with c2:
            log_level = st.selectbox("Log Level", LOG_LEVELS, index=0)

        # ── Credentials ────────────────────────────────────────────────────────
        with st.expander("Credentials override (leave blank to use config.json)"):
            token    = st.text_input("Bearer Token", type="password", placeholder="Leave blank to use config.json")
            st.markdown("*— or —*")
            uc1, uc2 = st.columns(2)
            with uc1: username = st.text_input("Username")
            with uc2: password = st.text_input("Password", type="password")

        # ── Advanced ───────────────────────────────────────────────────────────
        with st.expander("Advanced Options"):
            group_id              = st.text_input("Route Group ID",      placeholder="Leave blank for top-level routes")
            create_missing_group  = st.checkbox("Create group if missing")
            group_name            = st.text_input("Group Display Name",  placeholder="Used only when creating a missing group")
            st.markdown("**Safety Overrides** — leave blank to use config.json defaults")
            a1, a2 = st.columns(2)
            with a1: min_routes  = st.text_input("Min Existing Total Routes", placeholder=str(config.get("min_existing_total_routes", 1)))
            with a2: diff_lines  = st.text_input("Diff Context Lines",        placeholder=str(config.get("diff_lines", 3)))
            snapshot_dir = st.text_input("Snapshot Directory", placeholder=config.get("snapshot_dir", "cribl_snapshots"))
            log_file     = st.text_input("Log File Path (appended)",    placeholder="e.g. run.log")

        st.divider()
        run_clicked = st.button("Run cribl-pusher", type="primary", use_container_width=True)

    # ── RIGHT — Output ────────────────────────────────────────────────────────
    with right:
        st.subheader("Output")

        if st.session_state.last_returncode is not None:
            if st.session_state.last_returncode == 0:
                st.success("Completed successfully (exit code 0).")
            else:
                st.error(f"Finished with errors (exit code {st.session_state.last_returncode}).")

        out_placeholder = st.empty()
        if st.session_state.last_output:
            out_placeholder.code(st.session_state.last_output, language="")

    # ── Run logic ─────────────────────────────────────────────────────────────
    if run_clicked:
        errors = validate(
            mode      = "single" if mode == "Single App" else "bulk",
            appid     = appid,
            appname   = appname,
            uploaded_file = uploaded_file,
            token     = token,
            username  = username,
            password  = password,
            min_routes = min_routes,
            diff_lines = diff_lines,
        )
        if requires_allow and not allow_prod:
            errors.append(f"Workspace '{selected_ws}' requires the 'Allow production writes' checkbox.")

        if errors:
            with right:
                for e in errors:
                    st.error(e)
            st.stop()

        tmp_path = None
        try:
            if mode == "Bulk File" and uploaded_file is not None:
                with tempfile.NamedTemporaryFile(
                    mode="wb", suffix=".txt", delete=False, dir=SCRIPT_DIR
                ) as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name

            cmd = build_command(
                workspace             = selected_ws,
                allow_prod            = allow_prod,
                cribl_url             = cribl_url,
                mode                  = "single" if mode == "Single App" else "bulk",
                appid                 = appid,
                appname               = appname,
                appfile_path          = tmp_path or "",
                dry_run               = dry_run,
                skip_ssl              = skip_ssl,
                log_level             = log_level,
                log_file              = log_file,
                token                 = token,
                username              = username,
                password              = password,
                group_id              = group_id,
                create_missing_group  = create_missing_group,
                group_name            = group_name,
                min_routes            = min_routes,
                diff_lines            = diff_lines,
                snapshot_dir          = snapshot_dir,
            )

            masked = [
                "***" if i > 0 and cmd[i - 1] in ("--password", "--token") else part
                for i, part in enumerate(cmd)
            ]
            with right:
                with st.expander("Command"):
                    st.code(" ".join(masked), language="bash")
                with st.spinner("Running..."):
                    output, returncode = run_subprocess(cmd)

            st.session_state.last_output     = output
            st.session_state.last_returncode = returncode

            with right:
                if returncode == 0:
                    st.success("Completed successfully (exit code 0).")
                else:
                    st.error(f"Finished with errors (exit code {returncode}).")
                out_placeholder.code(output, language="")

        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ELK Roles + Cribl
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    rm_left, rm_right = st.columns([1, 1], gap="large")

    # ── LEFT — Form ───────────────────────────────────────────────────────────
    with rm_left:
        st.subheader("App Input")
        rm_mode = st.radio("Mode", ["Single App", "Bulk File"], horizontal=True, key="rm_mode")

        rm_apmid = rm_app_name = ""
        rm_uploaded_file = None

        if rm_mode == "Single App":
            rm_apmid    = st.text_input("App ID",   placeholder="app00001234",    key="rm_apmid")
            rm_app_name = st.text_input("App Name", placeholder="my-application", key="rm_app_name")
        else:
            st.caption("One entry per line: `appid, appname` — lines starting with `#` are ignored.")
            rm_uploaded_file = st.file_uploader("App list (.txt)", type=["txt"], key="rm_appfile")
            if rm_uploaded_file:
                raw   = rm_uploaded_file.getvalue().decode("utf-8", errors="replace")
                valid = [l for l in raw.splitlines() if l.strip() and not l.strip().startswith("#")]
                st.caption(f"{len(valid)} app(s) found in file.")
                with st.expander("Preview first 5"):
                    st.code("\n".join(valid[:5]) or "(empty)")
                rm_uploaded_file.seek(0)

        st.divider()

        st.subheader("ELK Connection")
        _elk_urls = config.get("elk_urls", [])
        if _elk_urls:
            rm_elk_url = st.selectbox("ELK URL", _elk_urls, key="rm_elk_url")
        else:
            rm_elk_url = st.text_input("ELK URL", placeholder="https://elk.example.com", key="rm_elk_url")
        rm_elk_token    = st.text_input("ELK Token",    type="password",
                                        placeholder="Overrides user/password if set", key="rm_elk_token")
        st.markdown("*— or user/password —*")
        ec1, ec2 = st.columns(2)
        with ec1: rm_elk_user     = st.text_input("ELK User",     key="rm_elk_user")
        with ec2: rm_elk_password = st.text_input("ELK Password", type="password", key="rm_elk_password")

        st.divider()

        st.subheader("Cribl Workspace")
        _cribl_urls = config.get("cribl_urls", [])
        if _cribl_urls:
            rm_cribl_url = st.selectbox("Cribl URL", _cribl_urls, key="rm_cribl_url")
        else:
            rm_cribl_url = st.text_input("Cribl URL", placeholder="https://cribl.example.com:9000",
                                         key="rm_cribl_url")
        rm_labels         = [ws_label(n, workspaces[n]) for n in ws_names]
        rm_selected_label = st.selectbox("Select workspace", rm_labels, key="rm_workspace")
        rm_selected_ws    = ws_names[rm_labels.index(rm_selected_label)]
        rm_ws_cfg         = workspaces[rm_selected_ws]
        rm_requires_allow = rm_ws_cfg.get("require_allow", False)

        if rm_requires_allow:
            st.warning(f"**{rm_selected_ws}** is a protected workspace.")
            rm_allow_prod = st.checkbox("Allow production writes (required for this workspace)", key="rm_allow_prod")
        else:
            rm_allow_prod = False

        st.divider()

        st.subheader("Options")
        ro1, ro2 = st.columns(2)
        with ro1:
            rm_dry_run  = st.checkbox("Dry Run (no writes)", value=True, key="rm_dry_run")
            rm_skip_ssl = st.checkbox("Skip SSL Verification",          key="rm_skip_ssl")
        with ro2:
            rm_log_level = st.selectbox("Log Level", LOG_LEVELS, index=0, key="rm_log_level")

        rm_order = st.radio("Order", ["ELK first", "Cribl first"], horizontal=True, key="rm_order")

        st.subheader("Skip sides")
        sk1, sk2 = st.columns(2)
        with sk1: rm_skip_elk   = st.checkbox("Skip ELK",   key="rm_skip_elk")
        with sk2: rm_skip_cribl = st.checkbox("Skip Cribl", key="rm_skip_cribl")

        st.divider()
        rm_run_clicked = st.button("Run rode_rm", type="primary", use_container_width=True, key="rm_run")

    # ── RIGHT — Output ────────────────────────────────────────────────────────
    with rm_right:
        st.subheader("Output")

        if st.session_state.rm_last_returncode is not None:
            if st.session_state.rm_last_returncode == 0:
                st.success("Completed successfully (exit code 0).")
            else:
                st.error(f"Finished with errors (exit code {st.session_state.rm_last_returncode}).")

        rm_out_placeholder = st.empty()
        if st.session_state.rm_last_output:
            rm_out_placeholder.code(st.session_state.rm_last_output, language="")

    # ── Run logic ─────────────────────────────────────────────────────────────
    if rm_run_clicked:
        rm_mode_key = "single" if rm_mode == "Single App" else "bulk"
        rm_errors = validate_rm(
            mode        = rm_mode_key,
            app_name    = rm_app_name,
            apmid       = rm_apmid,
            uploaded_file = rm_uploaded_file,
            elk_url     = rm_elk_url,
            elk_user    = rm_elk_user,
            elk_password= rm_elk_password,
            elk_token   = rm_elk_token,
            skip_elk    = rm_skip_elk,
            skip_cribl  = rm_skip_cribl,
        )
        if rm_requires_allow and not rm_allow_prod:
            rm_errors.append(f"Workspace '{rm_selected_ws}' requires the 'Allow production writes' checkbox.")

        if rm_errors:
            with rm_right:
                for e in rm_errors:
                    st.error(e)
            st.stop()

        rm_tmp_path = None
        try:
            if rm_mode_key == "bulk" and rm_uploaded_file is not None:
                with tempfile.NamedTemporaryFile(
                    mode="wb", suffix=".txt", delete=False, dir=SCRIPT_DIR
                ) as tmp:
                    tmp.write(rm_uploaded_file.getvalue())
                    rm_tmp_path = tmp.name

            rm_order_val = "elk-first" if rm_order == "ELK first" else "cribl-first"
            cmd_rm = build_command_rm(
                mode        = rm_mode_key,
                app_name    = rm_app_name,
                apmid       = rm_apmid,
                appfile_path= rm_tmp_path or "",
                elk_url     = rm_elk_url,
                elk_user    = rm_elk_user,
                elk_password= rm_elk_password,
                elk_token   = rm_elk_token,
                cribl_url   = rm_cribl_url,
                workspace   = rm_selected_ws,
                allow_prod  = rm_allow_prod,
                order       = rm_order_val,
                skip_elk    = rm_skip_elk,
                skip_cribl  = rm_skip_cribl,
                dry_run     = rm_dry_run,
                skip_ssl    = rm_skip_ssl,
                log_level   = rm_log_level,
            )

            masked_rm = [
                "***" if i > 0 and cmd_rm[i - 1] in ("--elk-password", "--elk-token") else part
                for i, part in enumerate(cmd_rm)
            ]
            with rm_right:
                with st.expander("Command"):
                    st.code(" ".join(masked_rm), language="bash")
                with st.spinner("Running..."):
                    rm_output, rm_returncode = run_subprocess(cmd_rm)

            st.session_state.rm_last_output     = rm_output
            st.session_state.rm_last_returncode = rm_returncode

            with rm_right:
                if rm_returncode == 0:
                    st.success("Completed successfully (exit code 0).")
                else:
                    st.error(f"Finished with errors (exit code {rm_returncode}).")
                rm_out_placeholder.code(rm_output, language="")

        finally:
            if rm_tmp_path and os.path.exists(rm_tmp_path):
                try:
                    os.unlink(rm_tmp_path)
                except OSError:
                    pass
