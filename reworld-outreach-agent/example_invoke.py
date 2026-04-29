#!/usr/bin/env python3
"""
One-shot example: invoke the deployed AgentCore Runtime with a single payload
and print the email. Runs the same shell pipeline a workshop attendee types:

    echo '{...}' | python invoke_runtime.py

…but as a self-contained Python file so there's no heredoc / quoting drama.

Run:
    cd reworld-outreach-agent
    python example_invoke.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PAYLOAD = {
    "full_name": "Tim K",
    "organization_name": "Republic Services",
    "state": "NJ",
    "industryName": "Environmental Services",
}


def main() -> int:
    here = Path(__file__).parent
    invoke_script = here / "invoke_runtime.py"
    if not invoke_script.exists():
        sys.exit(f"missing {invoke_script}")

    print(f"payload:\n  {json.dumps(PAYLOAD, indent=2)}\n")
    print("invoking runtime via invoke_runtime.py…\n")

    proc = subprocess.run(
        [sys.executable, str(invoke_script)],
        input=json.dumps(PAYLOAD),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(here),
    )
    if proc.returncode != 0:
        print(f"✗ invocation failed (exit {proc.returncode})")
        print(proc.stderr)
        return proc.returncode

    response = json.loads(proc.stdout.strip())
    print("response:")
    print(response["email"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
