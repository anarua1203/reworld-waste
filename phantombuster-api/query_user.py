#!/usr/bin/env python3
"""
Build a LinkedIn Sales Navigator search URL (filtered by company + region +
buying-committee titles) and either print it (--build-only) or feed it into a
PhantomBuster Sales-Nav-Search Phantom (--launch).

Example:
    python query_user.py --company "Republic Services" --state NJ --build-only
    python query_user.py --company "Republic Services" --state NJ --agent-id 1234567890
    python query_user.py --list-agents

Two hops are decoupled so you can sanity-check the URL in a browser before
spending Phantom credits.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from dotenv import dotenv_values

HERE = Path(__file__).parent
BASE = "https://api.phantombuster.com/api/v2"
TIMEOUT = 30

US_GEO_ID = "103644278"

STATE_URN: dict[str, str] = {
    "alabama": "102240587", "alaska": "100290991", "arizona": "106032500",
    "arkansas": "102790221", "california": "102095887", "colorado": "105763813",
    "connecticut": "106914527", "delaware": "105375497",
    "district of columbia": "101116121", "dc": "101116121",
    "florida": "101318387", "georgia": "103950076", "hawaii": "105051999",
    "idaho": "102560739", "illinois": "101949407", "indiana": "103336534",
    "iowa": "103078544", "kansas": "104403803", "kentucky": "106470801",
    "louisiana": "101822552", "maine": "101102875", "maryland": "100809221",
    "massachusetts": "101098412", "michigan": "103051080",
    "minnesota": "103411167", "mississippi": "106899551",
    "missouri": "101486475", "montana": "101758306", "nebraska": "101197782",
    "nevada": "101690912", "new hampshire": "103532695",
    "new jersey": "101651951", "new mexico": "105048220",
    "new york": "105080838", "north carolina": "103255397",
    "north dakota": "104611396", "ohio": "106981407", "oklahoma": "101343299",
    "oregon": "101685541", "pennsylvania": "102986501",
    "rhode island": "104877241", "south carolina": "102687171",
    "south dakota": "100115110", "tennessee": "104629187",
    "texas": "102748797", "utah": "104102239", "vermont": "104453637",
    "virginia": "101630962", "washington": "103977389",
    "west virginia": "106420769", "wisconsin": "104454774",
    "wyoming": "100658004",
}

STATE_ABBR: dict[str, str] = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut",
    "DE": "delaware", "DC": "district of columbia", "FL": "florida",
    "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky",
    "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana",
    "NE": "nebraska", "NV": "nevada", "NH": "new hampshire",
    "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio",
    "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming",
}

# Buying-committee title IDs from the Reworld contact-resolution playbook
# (Sustainability, EHS / Health & Waste, Waste Operations, Facilities,
#  Procurement, executive sponsors). These are LinkedIn Sales Navigator
# encoded title IDs — opaque to us, treated as constants.
TITLE_IDS = [
    462, 254, 2287, 19186, 2687, 19322, 957, 8918, 19010, 21503, 3512, 5178,
    1624, 600, 499, 8129, 9189, 3269, 16489, 368, 895, 3089, 1881, 5111, 4093,
    1579, 2470, 1435, 18083, 8503, 31785, 540, 1171, 2309, 1664, 6746, 2099,
    8933, 10991, 4230, 5245, 7843, 15821, 21243, 5644, 17658, 572, 2013, 1163,
    1684, 2318, 185, 174, 1219, 312, 725, 1525, 4891, 8322, 7568, 6100, 7573,
    22360, 6824, 333, 81, 2072,
]


def build_sales_nav_url(company: str, state: str) -> dict[str, Any]:
    """Replicates the n8n function-node logic that produces a Sales Nav URL."""
    company_text = "".join(c for c in company if c not in "(),")
    state_key = state.strip().lower()
    abbr_match = STATE_ABBR.get(state.strip().upper())
    if abbr_match:
        state_key = abbr_match
    state_geo_id = STATE_URN.get(state_key, "")
    region_id = state_geo_id or US_GEO_ID

    filters: list[str] = []
    if company_text:
        filters.append(
            f"(type:CURRENT_COMPANY,values:List((text:{company_text},"
            "selectionType:INCLUDED)))"
        )
    filters.append(
        f"(type:REGION,values:List((id:{region_id},selectionType:INCLUDED)))"
    )
    title_values = ",".join(
        f"(id:{tid},selectionType:INCLUDED)" for tid in TITLE_IDS
    )
    filters.append(f"(type:CURRENT_TITLE,values:List({title_values}))")

    query = f"(filters:List({','.join(filters)}))"
    base = "https://www.linkedin.com/sales/search/people"
    phantom_url = (
        f"{base}?query={quote(query, safe='')}"
        f"&nocache={int(time.time() * 1000)}"
    )

    return {
        "phantomUrl": phantom_url,
        "companyText": company_text,
        "stateInput": state,
        "stateKey": state_key,
        "stateGeoIdUsed": region_id,
        "defaultedToUS": not state_geo_id,
        "titleCount": len(TITLE_IDS),
    }


# ──────────────────────────────────────────────────────────────────────────────
# PhantomBuster client (direct, not via gateway — gateway exposes the same API)
# ──────────────────────────────────────────────────────────────────────────────

def env_key() -> str:
    env = dotenv_values(HERE / ".env")
    key = (env.get("PHANTOMBUSTER_API_KEY") or "").strip()
    if not key or key == "placeholder":
        sys.exit("PHANTOMBUSTER_API_KEY not set in .env")
    return key


def pb(method: str, path: str, *, key: str, params=None, body=None) -> dict:
    resp = requests.request(
        method,
        f"{BASE}{path}",
        headers={"X-Phantombuster-Key": key, "Accept": "application/json"},
        params=params,
        json=body,
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        sys.exit(f"{method} {path} → {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def list_agents(key: str) -> list[dict]:
    body = pb("GET", "/agents/fetch-all", key=key)
    return body if isinstance(body, list) else body.get("agents", body) or []


def launch_and_wait(key: str, agent_id: str, phantom_url: str, *, poll_every=8, max_wait=600) -> dict:
    """Launch a Phantom with `linkedInSearchUrl=phantom_url`, poll until done,
    return the parsed result-object JSON if available, otherwise the container."""
    print(f"  ▸ launching agent {agent_id}…")
    # `bonusArgument` is merged with the agent's saved argument — preserves
    # `identities`/`sessionCookie`/etc. configured in the UI. Sales-Nav-Search
    # reads the URL from `salesNavigatorSearchUrl`; older Phantoms used
    # `linkedInSearchUrl`/`searches`. Send all three; extras are ignored.
    launch = pb("POST", "/agents/launch", key=key, body={
        "id": agent_id,
        "bonusArgument": json.dumps({
            "salesNavigatorSearchUrl": phantom_url,
            "linkedInSearchUrl": phantom_url,
            "searches": phantom_url,
            "inputType": "salesNavigatorSearchUrl",
        }),
    })
    container_id = launch.get("containerId") or launch.get("data", {}).get("containerId")
    if not container_id:
        sys.exit(f"launch did not return a containerId: {launch}")
    print(f"  ▸ container {container_id} — polling…")

    waited = 0
    while waited < max_wait:
        out = pb(
            "GET", "/agents/fetch-output",
            key=key, params={"id": agent_id, "containerId": container_id},
        )
        status = out.get("status") or out.get("data", {}).get("status", "")
        print(f"      [{waited:>3}s] status={status}")
        if status not in ("running", "launch-queue", "starting"):
            break
        time.sleep(poll_every)
        waited += poll_every
    else:
        sys.exit(f"timed out after {max_wait}s; container {container_id} still running")

    container = pb("GET", "/containers/fetch-output", key=key, params={"id": container_id})
    result_url = container.get("resultObject") or container.get("data", {}).get("resultObject")
    if result_url:
        try:
            r = requests.get(result_url, timeout=TIMEOUT)
            r.raise_for_status()
            container["_resultPayload"] = r.json()
        except Exception as e:  # noqa: BLE001
            container["_resultFetchError"] = str(e)
    return container


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--company", help="Company name to filter on (CURRENT_COMPANY).")
    p.add_argument("--state", default="", help="USPS code or full name. Defaults to US-wide.")
    p.add_argument("--agent-id", help="PhantomBuster Sales-Nav-Search agent ID. Required to launch.")
    p.add_argument("--build-only", action="store_true", help="Print the URL and exit, no API calls.")
    p.add_argument("--list-agents", action="store_true", help="List Phantoms in the workspace and exit.")
    p.add_argument("--max-wait", type=int, default=600, help="Polling timeout in seconds (default 600).")
    args = p.parse_args()

    if args.list_agents:
        agents = list_agents(env_key())
        if not agents:
            print("No agents configured. Add one at https://phantombuster.com/phantoms")
            return 1
        print(f"{len(agents)} agent(s):")
        for a in agents:
            print(f"  id={a.get('id')}  name={a.get('name', '')[:60]}")
        return 0

    if not args.company:
        return p.error("--company is required (or use --list-agents)")

    info = build_sales_nav_url(args.company, args.state)
    print("── Sales Navigator URL ──────────────────────────────────────────────")
    for k in ("companyText", "stateInput", "stateKey", "stateGeoIdUsed", "defaultedToUS", "titleCount"):
        print(f"  {k:<16}= {info[k]}")
    print(f"\n  {info['phantomUrl']}\n")

    if args.build_only:
        return 0

    if not args.agent_id:
        print("(no --agent-id given; nothing launched. Use --list-agents to find one.)")
        return 0

    result = launch_and_wait(env_key(), args.agent_id, info["phantomUrl"], max_wait=args.max_wait)
    print("\n── Container result ────────────────────────────────────────────────")
    print(json.dumps(result, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
