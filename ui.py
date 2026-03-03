#!/usr/bin/env python3
"""
ui.py — Streamlit GUI wrapper for cribl-pusher.py

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
    workspace, allow_prod, mode, appid, appname, appfile_path,
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


# ── Page setup ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Cribl Pusher",
    page_icon=":satellite_antenna:",
    layout="wide",
)

st.title("Cribl Pusher")
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
if "last_output"     not in st.session_state: st.session_state.last_output     = ""
if "last_returncode" not in st.session_state: st.session_state.last_returncode = None

# ── Layout ────────────────────────────────────────────────────────────────────
left, right = st.columns([1, 1], gap="large")

# ══════════════════════════════════════════════════════════════════════════════
# LEFT — Form
# ══════════════════════════════════════════════════════════════════════════════
with left:
    st.subheader("Workspace")

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

    # ── App selection ──────────────────────────────────────────────────────────
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

    # ── Options ────────────────────────────────────────────────────────────────
    st.subheader("Options")
    c1, c2 = st.columns(2)
    with c1:
        dry_run  = st.checkbox("Dry Run (no writes)", value=True)
        skip_ssl = st.checkbox("Skip SSL Verification")
    with c2:
        log_level = st.selectbox("Log Level", LOG_LEVELS, index=0)

    # ── Credentials ────────────────────────────────────────────────────────────
    with st.expander("Credentials override (leave blank to use config.json)"):
        token    = st.text_input("Bearer Token", type="password", placeholder="Leave blank to use config.json")
        st.markdown("*— or —*")
        uc1, uc2 = st.columns(2)
        with uc1: username = st.text_input("Username")
        with uc2: password = st.text_input("Password", type="password")

    # ── Advanced ───────────────────────────────────────────────────────────────
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

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT — Output
# ══════════════════════════════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════════════════════════════
# Run logic
# ══════════════════════════════════════════════════════════════════════════════
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

        # Show masked command preview
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
