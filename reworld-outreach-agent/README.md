# Reworld Outreach Agent

A [Strands Agents](https://strandsagents.com) agent that connects to the PhantomBuster AgentCore Gateway over MCP and composes a strict-JSON outreach email.

## Architecture

```
stdin (JSON lead)
       │
       ▼
  agent.py  ─── BedrockModel (us.anthropic.claude-sonnet-4-6)
       │               │
       │         Strands Agent loop
       │               │
       ├── render_email_tool  (local @tool — Path A fast path)
       │
       └── MCPClient  ─── streamablehttp_client
                              │
                     Cognito M2M JWT (client_credentials)
                              │
                     AgentCore Gateway (phantombuster-poc-target___ tools)
```

**Path A** (default — all four template fields present): the agent calls `render_email_tool` locally, wraps the result in `{"email": "..."}`, and returns it. No Phantom credits consumed.

**Path B** (`--enrich` flag — company + state only): the agent calls `launchAgent` through the gateway, polls `fetchAgentOutput`, reads `fetchContainerOutput`, and returns `{"emails": [...]}` for each lead. **Only use with `--enrich` — Phantom launches consume pro_3 credits.**

## Why MCPClient and not raw JSON-RPC

The agent uses `strands.tools.mcp.MCPClient` so the Strands event-loop handles tool binding automatically — the model decides *when* to call *which* tool based on the system prompt context. Hand-crafting JSON-RPC calls (as in the `streamlit_app.py` sanity-checker) works fine for one-shot testing but bypasses the agent reasoning loop entirely.

## Prerequisites

- Python 3.12+
- pyenv / virtualenv with the packages in `requirements.txt`
- Valid AWS credentials with `bedrock:InvokeModel` permission for `us.anthropic.claude-sonnet-4-6`
- `.env` file (see `.env.example`)

## Setup

```bash
cd reworld-outreach-agent
pip install -r requirements.txt
cp .env.example .env
# Edit .env — fill in CLIENT_SECRET and AWS credentials
chmod 600 .env
```

## Local GUI

A Streamlit web app is included at `streamlit_app.py`. It provides:
- **Outreach composer** tab: click a sidebar preset or fill in the four lead fields and invoke the deployed AgentCore Runtime directly.
- **Gateway tool catalog** tab: browse and invoke the 8 MCP tools exposed by the PhantomBuster Gateway.
- **Find leads (Path B)** tab: run PhantomBuster enrichment (credit-gated, requires `PHANTOMBUSTER_API_KEY` and `PHANTOM_AGENT_ID`).

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The app reads `AGENT_RUNTIME_ARN` (plus the existing gateway env vars) from `.env`. Make sure `.env` is populated before starting.

## Local run

```bash
echo '{"full_name":"Timothy Kilpatrick","organization_name":"Republic Services","state":"NJ","industryName":"Environmental Services"}' \
  | python agent.py
```

Expected stdout:
```json
{"email": "Subject: Exploring compliant, sustainable waste solutions for Republic Services\n\nHello Timothy Kilpatrick,\n\nI work with Reworld Waste Solutions, supporting businesses across NJ with compliant, sustainable waste management. We specialize in helping teams reduce landfill risk, stay ahead of regulations, and convert waste into resources like renewable energy and recovered materials.\n\nWould you be open to a brief call to understand how you're currently handling Environmental Services waste and see if there's an opportunity to improve compliance or reduce cost?\n\nBest regards,"}
```

Exit code is `0` on success, non-zero on any parse / validation failure.

## Tests

```bash
# Unit tests only (no network calls)
python -m pytest tests/ -v -k "not live"

# Full integration tests (requires .env with valid credentials)
python -m pytest tests/ -v -m live

# All tests
python -m pytest tests/ -v
```

## AgentCore Runtime deployment

### Step 1 — Create IAM role

Create the `reworld-outreach-agent-runtime` IAM role with the trust policy below:

```bash
aws iam create-role \
  --role-name reworld-outreach-agent-runtime \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Principal":{"Service":"bedrock-agentcore.amazonaws.com"},
      "Action":"sts:AssumeRole"
    }]
  }'
```

Attach a permissions policy allowing `bedrock:InvokeModel`, `secretsmanager:GetSecretValue`, ECR pull, and CloudWatch Logs. See `runtime_deploy.py` → `PERMISSIONS_POLICY` for the exact JSON.

### Step 2 — Store CLIENT_SECRET in Secrets Manager (recommended)

```bash
aws secretsmanager create-secret \
  --name reworld/outreach-agent/client-secret \
  --secret-string '{"CLIENT_SECRET":"usu91f1sed7uto75..."}'
```

Then update `runtime_deploy.py` to inject the secret reference instead of the raw env var.

### Step 3 — Create ECR repository

```bash
python runtime_deploy.py --create-ecr --dry-run   # preview
python runtime_deploy.py --create-ecr              # execute
```

Or manually:
```bash
aws ecr create-repository --repository-name reworld-outreach-agent --region us-east-1
```

### Step 4 — Build, push, and register the Runtime

```bash
# Preview all steps
python runtime_deploy.py --dry-run

# Execute
python runtime_deploy.py
```

This builds the Docker image, pushes it to ECR, and calls `bedrock-agentcore-control.create_agent_runtime`.

### Step 5 — Invoke the Runtime

Once the runtime status is `READY`, invoke it via the AgentCore control plane endpoint or a configured AgentCore Endpoint. The payload shape is identical to the local CLI:

```json
{
  "full_name": "Timothy Kilpatrick",
  "organization_name": "Republic Services",
  "state": "NJ",
  "industryName": "Environmental Services"
}
```

## Project layout

```
reworld-outreach-agent/
├── agent.py              # Strands agent — BedrockModel + MCPClient + JSON guard
├── mcp_auth.py           # Cognito M2M token fetch + cached transport factory
├── templates.py          # Verbatim email template + render_email helper
├── entrypoint.py         # AgentCore Runtime handler (BedrockAgentCoreApp)
├── runtime_deploy.py     # ECR + Runtime registration script (don't auto-run)
├── Dockerfile            # Container spec (python:3.12-slim, port 8080)
├── requirements.txt      # Runtime dependencies
├── .env.example          # Credential template (commit this)
├── .env                  # Real credentials (never commit — in .gitignore)
├── .gitignore
├── tests/
│   └── test_agent_local.py
└── README.md
```

## Path B — lead enrichment (stretch goal)

The hook is in `agent.py:run_agent` — when `--enrich` is passed the agent receives a prompt instructing it to call the Phantom MCP tools in sequence. The `build_sales_nav_url` helper from `phantombuster-api/query_user.py` is the URL builder to import (already debugged with correct `STATE_URN` / `STATE_ABBR` / `TITLE_IDS` tables).

**Do not run `--enrich` without verifying the Phantom agent ID and checking credit balance first.**
