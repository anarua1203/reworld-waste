"""
Reworld Outreach Composer — Streamlit GUI.

Drives the deployed AgentCore Runtime (Path A: compose email from 4 fields)
and the underlying PhantomBuster AgentCore Gateway (Tool catalog + direct
MCP tool calls). Optionally runs Path B (PhantomBuster enrichment) behind an
explicit credit-consumption confirmation.

Run locally:
    pip install -r requirements.txt
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import requests
import streamlit as st
from dotenv import dotenv_values

# ─── DNS fallback ────────────────────────────────────────────────────────────
# macOS's mDNSResponder occasionally caches NXDOMAIN for freshly-created
# Cognito hosted-UI domains. Fall back to `dig` before surfacing the error.
_orig_getaddrinfo = socket.getaddrinfo


def _resilient_getaddrinfo(host, port, *args, **kwargs):
    try:
        return _orig_getaddrinfo(host, port, *args, **kwargs)
    except socket.gaierror:
        try:
            out = subprocess.run(
                ["dig", "+short", "+time=2", "+tries=1", str(host)],
                capture_output=True, text=True, timeout=4,
            ).stdout
            ips = [
                line.strip() for line in out.splitlines()
                if line.strip() and line.strip()[0].isdigit() and line.count(".") == 3
            ]
            if not ips:
                raise
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ips[0], int(port)))]
        except Exception:
            raise


socket.getaddrinfo = _resilient_getaddrinfo

# ─── Config — env merge order: .env → credentials.env → real env ─────────────
HERE = Path(__file__).parent
ENV = {
    **dotenv_values(HERE / ".env"),
    **dotenv_values(HERE / "credentials.env"),
    **{
        k: v for k, v in os.environ.items()
        if k.startswith((
            "AGENT_RUNTIME_ARN", "GATEWAY_", "CLIENT_", "TOKEN_",
            "SCOPE", "AWS_", "PHANTOM", "BEDROCK_",
        ))
    },
}


def _required(key: str, missing_hint: str = "") -> str:
    v = ENV.get(key, "").strip()
    if v:
        return v
    msg = f"Missing `{key}` in `.env` / `credentials.env`."
    if missing_hint:
        msg += f"\n\n{missing_hint}"
    st.error(msg)
    st.stop()
    raise RuntimeError("unreachable")  # satisfies type checker; st.stop halts


def _optional(key: str, default: str = "") -> str:
    return ENV.get(key, default).strip() or default


# ── AgentCore Runtime (Path A / compose) ────────────────────────────────────
AGENT_RUNTIME_ARN = _required(
    "AGENT_RUNTIME_ARN",
    missing_hint=(
        "Retrieve it with:\n"
        "```bash\n"
        "aws bedrock-agentcore-control list-agent-runtimes \\\n"
        "  --query 'agentRuntimes[?agentRuntimeName==`reworld_outreach_agent`].agentRuntimeArn'\n"
        "```\n"
        "Or with boto3:\n"
        "```python\n"
        "import boto3\n"
        "c = boto3.client('bedrock-agentcore-control', region_name='us-east-1')\n"
        "arns = [r['agentRuntimeArn'] for r in c.list_agent_runtimes()['agentRuntimes']\n"
        "        if r['agentRuntimeName'] == 'reworld_outreach_agent']\n"
        "print(arns[0])\n"
        "```"
    ),
)

# ── Gateway / MCP (Tool catalog tab) ─────────────────────────────────────────
GATEWAY_URL  = _required("GATEWAY_URL")
CLIENT_ID    = _required("CLIENT_ID")
CLIENT_SECRET = _required("CLIENT_SECRET")
TOKEN_URL    = _required("TOKEN_URL")
SCOPE        = _optional(
    "SCOPE",
    "phantombuster-api-gateway-id/gateway:read phantombuster-api-gateway-id/gateway:write",
)
AWS_REGION   = _optional("AWS_REGION", "us-east-1")

# ── PhantomBuster Path B (optional) ──────────────────────────────────────────
PHANTOMBUSTER_API_KEY = _optional("PHANTOMBUSTER_API_KEY")
PHANTOM_AGENT_ID      = _optional("PHANTOM_AGENT_ID")

# ─── Sidebar presets ─────────────────────────────────────────────────────────
# First 5 are real scraped leads from the workshop run; last 3 are synthetic demo.
PRESETS = [
    {
        "label": "Tim Kilpatrick — Fleet Maintenance",
        "args": {
            "full_name": "Timothy Kilpatrick",
            "organization_name": "Republic Services",
            "state": "NJ",
            "industryName": "Environmental Services",
        },
        "why": "Fleet Maintenance Manager — operations-side buyer in the title-filter hit set.",
        "real": True,
    },
    {
        "label": "Rebecca Reed — Procurement",
        "args": {
            "full_name": "Rebecca Reed",
            "organization_name": "Republic Services",
            "state": "NJ",
            "industryName": "Procurement",
        },
        "why": "Procurement Manager — exercises the industry token swap (Procurement vs Environmental Services).",
        "real": True,
    },
    {
        "label": "Sheena McCarthy — EHS",
        "args": {
            "full_name": "Sheena McCarthy",
            "organization_name": "Republic Services",
            "state": "NJ",
            "industryName": "Environmental Services",
        },
        "why": "Environmental Manager — the EHS persona Reworld targets.",
        "real": True,
    },
    {
        "label": "Lorena Mercado — Compliance",
        "args": {
            "full_name": "Lorena Mercado",
            "organization_name": "Republic Services",
            "state": "NJ",
            "industryName": "DOT Compliance",
        },
        "why": "Compliance Coordinator — long tenure persona, regulatory angle.",
        "real": True,
    },
    {
        "label": "Mindy Baker — Safety",
        "args": {
            "full_name": "Mindy Baker",
            "organization_name": "Republic Services",
            "state": "NJ",
            "industryName": "Safety",
        },
        "why": "OSHA Safety Manager — workplace-safety angle on the email body.",
        "real": True,
    },
    {
        "label": "Sample lead — Casella, VT",
        "args": {
            "full_name": "Jordan Lee",
            "organization_name": "Casella Waste Systems",
            "state": "VT",
            "industryName": "Sustainability",
        },
        "why": "Different region (Vermont) + Sustainability title — verifies state token works.",
        "real": False,
    },
    {
        "label": "Sample lead — Waste Connections, TX",
        "args": {
            "full_name": "Maria Gonzalez",
            "organization_name": "Waste Connections",
            "state": "TX",
            "industryName": "Operations",
        },
        "why": "Texas region, Operations title — wide geo coverage demo.",
        "real": False,
    },
    {
        "label": "Sample lead — Clean Harbors, MA",
        "args": {
            "full_name": "David Chen",
            "organization_name": "Clean Harbors",
            "state": "MA",
            "industryName": "Hazardous Waste",
        },
        "why": "Hazardous waste industry term — shows industryName flexibility.",
        "real": False,
    },
]


# ─── Cognito token cache (TTL 3300s = 55 min, matches nasa-api pattern) ──────
@st.cache_data(ttl=3300, show_spinner=False)
def _fetch_token(client_id: str, client_secret: str, token_url: str, scope: str) -> dict:
    """Fetch a Cognito M2M token. Cached for 55 min (token TTL is 60 min)."""
    r = requests.post(
        token_url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials", "scope": scope},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    body["_fetched_at"] = time.time()
    return body


def get_token() -> str:
    return _fetch_token(CLIENT_ID, CLIENT_SECRET, TOKEN_URL, SCOPE)["access_token"]


def token_age_seconds() -> int:
    fetched = _fetch_token(CLIENT_ID, CLIENT_SECRET, TOKEN_URL, SCOPE).get("_fetched_at", 0)
    return int(time.time() - fetched)


# ─── MCP helpers (Gateway / Tool catalog) ────────────────────────────────────
def _mcp(method: str, params: dict | None = None, _id: int = 1) -> dict:
    r = requests.post(
        GATEWAY_URL,
        headers={
            "Authorization": f"Bearer {get_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=600, show_spinner=False)
def list_tools() -> list[dict]:
    """Fetch the tool list from the gateway. Cached for 10 min."""
    resp = _mcp("tools/list")
    return resp.get("result", {}).get("tools", [])


def call_mcp_tool(name: str, arguments: dict) -> dict:
    """Invoke an MCP tool directly via the gateway."""
    resp = _mcp("tools/call", {"name": name, "arguments": arguments})
    if "error" in resp:
        return {"_error": resp["error"]}
    result = resp.get("result", {})
    content = result.get("content", [])
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return {"_text": content[0]["text"]}
    return result


# ─── AgentCore Runtime invocation (Path A) ───────────────────────────────────
def invoke_runtime(payload: dict) -> tuple[dict, float]:
    """Call the AgentCore Runtime via the data-plane API.

    Returns (parsed_result, elapsed_seconds).
    Raises RuntimeError on non-200 or unparseable response.
    """
    client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
    body_bytes = json.dumps(payload).encode("utf-8")

    t0 = time.time()
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            contentType="application/json",
            accept="application/json",
            payload=body_bytes,
        )
    except client.exceptions.ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]
        runtime_id = AGENT_RUNTIME_ARN.split("/")[-1] if "/" in AGENT_RUNTIME_ARN else AGENT_RUNTIME_ARN
        raise RuntimeError(
            f"AgentCore Runtime returned **{code}**: {msg}\n\n"
            f"Check CloudWatch logs: `/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT`"
        ) from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    elapsed = time.time() - t0
    status_code = resp.get("statusCode", 200)

    streaming_body = resp.get("response")
    if streaming_body is None:
        raise RuntimeError("Runtime response did not contain a body.")

    raw = streaming_body.read()
    if status_code >= 400:
        runtime_id = AGENT_RUNTIME_ARN.split("/")[-1] if "/" in AGENT_RUNTIME_ARN else AGENT_RUNTIME_ARN
        raise RuntimeError(
            f"Runtime returned HTTP {status_code}.\n\n"
            f"Raw response (first 400 chars): {raw[:400]!r}\n\n"
            f"Check CloudWatch logs: `/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT`"
        )

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Runtime response is not valid JSON: {exc}\n\nRaw (first 400 chars): {raw[:400]!r}"
        ) from exc

    return result, elapsed


# ─── Email renderer ───────────────────────────────────────────────────────────
def render_email_panel(result: dict, elapsed: float) -> None:
    """Render the {"email": "..."} result as a formatted email panel."""
    if "_error" in result:
        st.error(f"Agent error: {result['_error']}")
        return

    if "emails" in result:
        emails = result["emails"]
        st.success(f"{len(emails)} email(s) composed in {elapsed:.1f}s")
        for i, email_body in enumerate(emails, 1):
            with st.expander(f"Email {i}", expanded=(i == 1)):
                _render_single_email(email_body)
        with st.expander("Raw JSON"):
            st.json(result)
        return

    if "email" not in result:
        st.warning("Unexpected response shape — no `email` or `emails` key.")
        st.json(result)
        return

    email_body: str = result["email"]
    st.success(f"Email composed in {elapsed:.1f}s")
    _render_single_email(email_body)
    with st.expander("Raw JSON"):
        st.json(result)


def _render_single_email(email_body: str) -> None:
    """Render a single email string with subject header and copy-friendly body."""
    lines = email_body.splitlines()

    # Extract subject from first line (starts with "Subject: ")
    subject = ""
    body_start = 0
    if lines and lines[0].startswith("Subject: "):
        subject = lines[0][len("Subject: "):]
        body_start = 1
        # Skip blank line after subject
        if len(lines) > 1 and not lines[1].strip():
            body_start = 2

    body_text = "\n".join(lines[body_start:])

    st.markdown("**Subject:**")
    st.subheader(subject)
    st.divider()

    # Bordered container for email body
    with st.container(border=True):
        st.text(body_text)

    # st.code gives a copy icon automatically — no extra button needed
    st.caption("Copy email body:")
    st.code(body_text, language=None)


# ─── MCP tool result renderer (gateway catalog tab) ──────────────────────────
def render_mcp_result(tool_short: str, payload: dict) -> None:
    """Pretty-print gateway tool results. Falls back to JSON view."""
    if "_error" in payload:
        st.error(f"Tool error: {payload['_error']}")
        return
    if "_text" in payload:
        st.text(payload["_text"])
        return
    st.json(payload)
    with st.expander("Raw JSON"):
        st.json(payload)


# ─── Path B: PhantomBuster lead enrichment ───────────────────────────────────
def run_path_b(company: str, state: str) -> None:
    """Drive Path B enrichment via PhantomBuster API directly."""
    # Import helpers from the phantombuster-api sibling project.
    # Resolve the path relative to this file so it works regardless of cwd.
    pb_path = HERE.parent / "phantombuster-api"
    if str(pb_path) not in sys.path:
        sys.path.insert(0, str(pb_path))

    try:
        from query_user import build_sales_nav_url, launch_and_wait  # type: ignore[import]
    except ImportError as exc:
        st.error(
            f"Could not import PhantomBuster helpers from `{pb_path}`. "
            f"Make sure `phantombuster-api/query_user.py` is present.\n\nError: {exc}"
        )
        return

    if not PHANTOMBUSTER_API_KEY:
        st.error("Missing `PHANTOMBUSTER_API_KEY` in `.env`. Required for Path B enrichment.")
        return
    if not PHANTOM_AGENT_ID:
        st.error("Missing `PHANTOM_AGENT_ID` in `.env`. Required for Path B enrichment.")
        return

    url_info = build_sales_nav_url(company, state)
    phantom_url = url_info["phantomUrl"]
    st.info(
        f"Built Sales Navigator URL for **{url_info['companyText']}** / "
        f"**{url_info['stateInput']}** with {url_info['titleCount']} title filters."
    )

    log_container = st.empty()
    log_lines: list[str] = []

    def _log(msg: str) -> None:
        log_lines.append(msg)
        log_container.code("\n".join(log_lines), language=None)

    _log(f"Launching Phantom {PHANTOM_AGENT_ID}…")
    with st.spinner("Running PhantomBuster enrichment (this may take 2-5 min)…"):
        t0 = time.time()
        try:
            container = launch_and_wait(
                PHANTOMBUSTER_API_KEY, PHANTOM_AGENT_ID, phantom_url,
                poll_every=10, max_wait=600,
            )
        except SystemExit as exc:
            st.error(f"PhantomBuster launch failed: {exc}")
            return
        elapsed = time.time() - t0

    _log(f"Done in {elapsed:.0f}s")
    payload = container.get("_resultPayload")
    if payload:
        if isinstance(payload, list):
            st.success(f"{len(payload)} lead(s) returned")
            st.dataframe(payload, use_container_width=True)
        else:
            st.json(payload)
    else:
        st.warning("No result payload — check PhantomBuster console for details.")
        with st.expander("Raw container output"):
            st.json(container)


# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Reworld Outreach Composer",
    page_icon="♻️",
    layout="wide",
)

st.markdown("""
<style>
  .block-container { padding-top: 2rem; padding-bottom: 2rem; max-width: 1200px; }
  h1 { font-weight: 600; letter-spacing: -0.02em; }
  .stButton > button { width: 100%; text-align: left; font-weight: 400; }
  div[data-testid="stMetricLabel"] { font-size: 0.8rem; opacity: 0.7; }
  code { font-size: 0.85em; }
</style>
""", unsafe_allow_html=True)

st.title("♻️  Reworld Outreach Composer")
st.caption(
    "Compose personalized outreach emails via the deployed AgentCore Runtime, "
    "inspect the underlying PhantomBuster Gateway tools, or run lead enrichment (Path B)."
)

# ─── Status bar ──────────────────────────────────────────────────────────────
status_cols = st.columns([3, 2, 2, 1])
with status_cols[0]:
    arn_short = f"…{AGENT_RUNTIME_ARN[-60:]}" if len(AGENT_RUNTIME_ARN) > 60 else AGENT_RUNTIME_ARN
    st.markdown(f"**Runtime:** `{arn_short}`")
with status_cols[1]:
    try:
        age = token_age_seconds()
        ttl_min = max(0, 60 - age // 60)
        st.markdown(f"**Token:** valid · refreshes in ~{ttl_min} min")
    except Exception as e:
        st.error(f"Token fetch failed: {e}")
        st.stop()
with status_cols[2]:
    try:
        tools = list_tools()
        st.markdown(f"**Gateway tools:** {len(tools)} discovered")
    except Exception as e:
        st.warning(f"tools/list unavailable: {e}")
        tools = []
with status_cols[3]:
    if st.button("↻ Refresh", help="Clear token + tool cache"):
        st.cache_data.clear()
        st.rerun()

st.divider()

# ─── Sidebar — example prompts ───────────────────────────────────────────────
with st.sidebar:
    st.header("Example prompts")
    st.caption(
        "Click any lead to pre-fill the form and invoke the agent immediately. "
        "Real scraped leads are marked [scraped]; others are synthetic demos."
    )

    selected_preset: dict | None = None

    st.markdown("**Real scraped leads** (from workshop run)")
    for i, p in enumerate(PRESETS):
        if not p["real"]:
            continue
        btn_label = p["label"]
        if st.button(btn_label, key=f"preset_{i}", help=p["why"]):
            selected_preset = p

    st.markdown("**Synthetic demo leads**")
    for i, p in enumerate(PRESETS):
        if p["real"]:
            continue
        btn_label = p["label"]
        if st.button(btn_label, key=f"preset_{i}", help=p["why"]):
            selected_preset = p

    st.divider()
    st.caption(
        "Each preset exercises a different lead profile: "
        "different states, industry terms, and job functions."
    )

# ─── Main tabs ───────────────────────────────────────────────────────────────
tab_compose, tab_catalog, tab_enrich = st.tabs([
    "✉ Outreach composer",
    "📖 Gateway tool catalog",
    "🔍 Find leads (Path B)",
])

# Session state for result history
if "compose_history" not in st.session_state:
    st.session_state.compose_history = []  # list of (label, result, elapsed)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: Outreach Composer
# ══════════════════════════════════════════════════════════════════════════════
with tab_compose:
    col_form, col_result = st.columns([1, 2], gap="large")

    with col_form:
        st.subheader("Lead details")

        # Pre-fill from selected preset
        preset_args = selected_preset["args"] if selected_preset else {}
        default_name  = preset_args.get("full_name", "")
        default_org   = preset_args.get("organization_name", "")
        default_state = preset_args.get("state", "")
        default_ind   = preset_args.get("industryName", "")

        with st.form("compose_form"):
            full_name = st.text_input(
                "Full name *",
                value=default_name,
                placeholder="e.g. Timothy Kilpatrick",
            )
            organization_name = st.text_input(
                "Organization *",
                value=default_org,
                placeholder="e.g. Republic Services",
            )
            state = st.text_input(
                "State *",
                value=default_state,
                placeholder="e.g. NJ",
                max_chars=50,
            )
            industry_name = st.text_input(
                "Industry / function *",
                value=default_ind,
                placeholder="e.g. Environmental Services",
            )
            compose_btn = st.form_submit_button(
                "Compose email",
                type="primary",
                use_container_width=True,
            )

        if selected_preset:
            st.caption(f"Loaded preset: **{selected_preset['label']}**")
            st.caption(selected_preset["why"])

    with col_result:
        st.subheader("Result")

        # Trigger composition — either from preset click or form submit
        trigger_args: dict | None = None
        trigger_label: str = ""

        if selected_preset and not compose_btn:
            # Sidebar button click: auto-invoke with preset args
            trigger_args = selected_preset["args"]
            trigger_label = selected_preset["label"]
        elif compose_btn:
            # Form submit: validate and invoke
            if not all([full_name.strip(), organization_name.strip(), state.strip(), industry_name.strip()]):
                st.error("All four fields are required.")
            else:
                trigger_args = {
                    "full_name": full_name.strip(),
                    "organization_name": organization_name.strip(),
                    "state": state.strip(),
                    "industryName": industry_name.strip(),
                }
                trigger_label = f"{full_name} @ {organization_name}"

        if trigger_args is not None:
            with st.spinner(f"Invoking AgentCore Runtime… (typically 5-12s)"):
                try:
                    result, elapsed = invoke_runtime(trigger_args)
                    st.session_state.compose_history.insert(0, (trigger_label, result, elapsed))
                except RuntimeError as exc:
                    st.error(str(exc))
                    result = None

            if result is not None:
                render_email_panel(result, elapsed)

        elif st.session_state.compose_history:
            # Show most recent result on page refresh / tab switch
            label, result, elapsed = st.session_state.compose_history[0]
            st.caption(f"Most recent: **{label}**")
            render_email_panel(result, elapsed)

        else:
            st.info("Pick a lead from the sidebar or fill in the form to compose an email.")

    # Earlier results
    if len(st.session_state.compose_history) > 1:
        with st.expander(f"Earlier results ({len(st.session_state.compose_history) - 1})"):
            for label, result, elapsed in st.session_state.compose_history[1:6]:
                st.markdown(f"**{label}** · {elapsed:.1f}s")
                if "email" in result:
                    lines = result["email"].splitlines()
                    subject = lines[0].replace("Subject: ", "") if lines else ""
                    st.caption(subject)
                st.json(result, expanded=False)
                st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: Gateway Tool Catalog
# ══════════════════════════════════════════════════════════════════════════════
with tab_catalog:
    if not tools:
        st.warning("Gateway tool list could not be loaded. Check `GATEWAY_URL` and Cognito credentials.")
    else:
        catalog_tab, custom_tab = st.tabs(["Tool catalog", "Custom call"])

        with catalog_tab:
            st.write(
                f"All **{len(tools)}** tools exposed by the PhantomBuster AgentCore Gateway, "
                "with their JSON-Schema input contracts."
            )
            TOOL_PREFIX = "phantombuster-poc-target___"
            for t in tools:
                short = t["name"].replace(TOOL_PREFIX, "")
                with st.expander(f"`{short}` — {t.get('description', '')[:90]}"):
                    st.markdown(f"**Full name:** `{t['name']}`")
                    st.markdown(f"**Description:** {t.get('description', '')}")
                    st.markdown("**Input schema:**")
                    st.json(t.get("inputSchema", {}), expanded=False)

        with custom_tab:
            st.write("Pick any gateway tool and fill in arguments by hand.")
            TOOL_PREFIX = "phantombuster-poc-target___"
            short_names = [t["name"].replace(TOOL_PREFIX, "") for t in tools]
            pick = st.selectbox("Tool", short_names, key="catalog_tool_pick")
            chosen = next(t for t in tools if t["name"].endswith(pick))
            schema = chosen.get("inputSchema", {})
            props = schema.get("properties", {})
            required_fields = set(schema.get("required", []))

            with st.form("custom_mcp_call"):
                args: dict[str, Any] = {}
                for fname, spec in props.items():
                    lbl = f"{fname}{' *' if fname in required_fields else ''}"
                    help_text = spec.get("description", "")
                    ftype = spec.get("type")
                    if ftype == "integer":
                        v = st.number_input(lbl, value=0, step=1, help=help_text)
                        if v != 0 or fname in required_fields:
                            args[fname] = int(v)
                    elif ftype == "number":
                        v = st.number_input(lbl, value=0.0, help=help_text, format="%.4f")
                        if v != 0.0 or fname in required_fields:
                            args[fname] = float(v)
                    elif spec.get("enum"):
                        opts = [""] + list(spec["enum"])
                        v = st.selectbox(lbl, opts, help=help_text)
                        if v:
                            args[fname] = v
                    else:
                        v = st.text_input(lbl, help=help_text)
                        if v:
                            args[fname] = v
                submit_mcp = st.form_submit_button("Invoke", type="primary")

            if submit_mcp:
                with st.spinner(f"Calling {pick}…"):
                    try:
                        mcp_result = call_mcp_tool(chosen["name"], args)
                        render_mcp_result(pick, mcp_result)
                    except Exception as exc:
                        st.error(f"Call failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: Find Leads (Path B — PhantomBuster enrichment)
# ══════════════════════════════════════════════════════════════════════════════
with tab_enrich:
    st.subheader("PhantomBuster lead enrichment (Path B)")
    st.markdown(
        "This tab launches a LinkedIn Sales Navigator Search Export Phantom to find "
        "buying-committee leads for a given company and state, then polls for results.\n\n"
        "**This consumes PhantomBuster credits.** Use with care."
    )

    credits_ok = st.checkbox(
        "I understand this consumes PhantomBuster credits and I want to proceed.",
        value=False,
        key="credits_confirmed",
    )

    if not credits_ok:
        st.info("Check the box above to enable Path B enrichment.")
    else:
        if not PHANTOMBUSTER_API_KEY or not PHANTOM_AGENT_ID:
            st.error(
                "Missing `PHANTOMBUSTER_API_KEY` and/or `PHANTOM_AGENT_ID` in `.env`. "
                "Both are required for Path B enrichment."
            )
        else:
            pb_path = HERE.parent / "phantombuster-api"
            if not pb_path.exists():
                st.error(
                    f"PhantomBuster helpers not found at `{pb_path}`. "
                    "Make sure `phantombuster-api/query_user.py` is present alongside this project."
                )
            else:
                with st.form("enrich_form"):
                    enrich_company = st.text_input(
                        "Company name *",
                        placeholder="e.g. Republic Services",
                    )
                    enrich_state = st.text_input(
                        "State *",
                        placeholder="e.g. NJ",
                        max_chars=50,
                    )
                    enrich_btn = st.form_submit_button("Find leads", type="primary")

                if enrich_btn:
                    if not enrich_company.strip() or not enrich_state.strip():
                        st.error("Company name and state are required.")
                    else:
                        run_path_b(enrich_company.strip(), enrich_state.strip())
