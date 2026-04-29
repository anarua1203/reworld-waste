# Reworld POC — Deploy to a New AWS Account

End-to-end runbook for replicating the PhantomBuster AgentCore Gateway plus the Reworld Outreach Strands Agent (deployed to AgentCore Runtime) in a fresh AWS account.

Two artifacts to deploy:

1. **`phantombuster-api/`** — AgentCore Gateway exposing PhantomBuster's REST API as 8 MCP tools (`fetchOrgInfo`, `fetchAllAgents`, `launchAgent`, etc.). Requires Cognito M2M, IAM role, S3 spec, credential provider, gateway target.
2. **`reworld-outreach-agent/`** — Strands agent (BedrockModel + MCPClient → the gateway) packaged as a container and registered as an AgentCore Runtime.

Total provisioning time on a fresh account: **~15-25 min** including Docker build/push.

---

## 0. Prerequisites — external (not AWS)

### PhantomBuster
- Active PhantomBuster account on a paid plan (Trial works for the gateway, but `launchType: "repeatedly"` Phantoms need a paid plan for reliable API launches).
- Workspace API key — copy from `https://phantombuster.com/<workspace-id>/workspace-settings#api-keys`.
- A configured Phantom that takes a Sales Nav search URL — typically *LinkedIn Sales Navigator Search Export*. The Phantom must have:
  - LinkedIn `sessionCookie` (`li_at` value) configured **once via the UI**.
  - `launchType` set to `repeatedly` (not `manually`) so API launches are accepted.
  - Note its agent ID (16-digit number from the URL).

### Local tools
- Python 3.12+
- Docker Desktop with buildx enabled (Apple Silicon defaults to arm64; the runtime requires linux/arm64)
- `pip install boto3 python-dotenv requests strands-agents bedrock-agentcore` (full list in `requirements.txt` of each project)
- Git (to clone this workspace)

---

## 1. AWS account prerequisites

### 1.1 IAM user with long-lived access keys
**AgentCore rejects SSO / federated / assumed-role credentials** during gateway/runtime creation. You must use an IAM user with a static access key.

- IAM Console → Users → Create user → attach the policy below.
- Generate a *long-lived* access key (the script bails on STS-style ARNs).

Policy (broad for the POC; tighten before production):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["iam:*", "sts:GetCallerIdentity"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["cognito-idp:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["s3:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["ecr:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["bedrock:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["bedrock-agentcore:*", "bedrock-agentcore-control:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["secretsmanager:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["logs:*"], "Resource": "*" }
  ]
}
```

### 1.2 Bedrock model access
The agent uses `us.anthropic.claude-sonnet-4-6` (cross-region inference profile). Enable model access:

- Bedrock Console → Model access → Manage access → enable **Claude Sonnet 4.6** (or whichever Sonnet model you want — update `BEDROCK_MODEL_ID` accordingly).
- Wait for status to show *Access granted*. This can be instant or take a few minutes.

Verify from your shell:
```bash
python3 -c "
import boto3
b = boto3.client('bedrock', region_name='us-east-1')
for p in b.list_inference_profiles()['inferenceProfileSummaries']:
    if 'sonnet-4-6' in p['inferenceProfileId']: print(p['inferenceProfileArn'])
"
```
Expect: `arn:aws:bedrock:us-east-1:<account>:inference-profile/us.anthropic.claude-sonnet-4-6`

### 1.3 Region
This guide assumes `us-east-1`. If you choose another region, update `AWS_REGION` everywhere. Confirm AgentCore Runtime is GA in your target region first — at time of writing, GA regions are limited.

---

## 2. Clone and configure

```bash
git clone <this-repo> reworld-poc && cd reworld-poc
# Or copy the two project directories into your workspace.
```

### 2.1 Configure `phantombuster-api/.env`

Copy the template and fill new values:
```bash
cp phantombuster-api/.env.example phantombuster-api/.env
chmod 600 phantombuster-api/.env
```

Replace these in `phantombuster-api/.env`:

| Variable | What to fill in |
|---|---|
| `AWS_ACCOUNT_ID` | Your 12-digit AWS account ID |
| `AWS_ACCESS_KEY_ID` | Long-lived IAM user access key (NOT SSO) |
| `AWS_SECRET_ACCESS_KEY` | Matching secret access key |
| `PHANTOMBUSTER_API_KEY` | Workspace API key from `https://phantombuster.com/<workspace-id>/workspace-settings#api-keys` |

Leave alone (or rename if you want different resource names):
- `USER_POOL_NAME`, `RESOURCE_SERVER_ID`, `CLIENT_NAME`
- `GATEWAY_NAME`, `TARGET_NAME`, `IAM_ROLE_NAME`
- `OPENAPI_SPEC_FILE=phantombuster-v2.json`
- `CREDENTIAL_PROVIDER_NAME`, `CREDENTIAL_PARAMETER_NAME=X-Phantombuster-Key`, `CREDENTIAL_LOCATION=HEADER`

### 2.2 Configure `reworld-outreach-agent/.env`

```bash
cp reworld-outreach-agent/.env.example reworld-outreach-agent/.env
chmod 600 reworld-outreach-agent/.env
```

Pre-fill the AWS section (account id + IAM keys, same as gateway). The Cognito-side fields (`GATEWAY_URL`, `TOKEN_URL`, `CLIENT_ID`, `CLIENT_SECRET`, `SCOPE`) get populated automatically by `get_credentials.py` in the next stage — leave them blank for now.

---

## 3. Deploy the gateway

```bash
cd phantombuster-api
pip install -r requirements.txt
python phantombuster_api_gateway_setup.py
```

This is idempotent — it creates or reuses each resource by name. On a fresh account it will:

1. Create IAM role `agentcore-phantombuster-api-gateway-role`.
2. Create Cognito user pool, resource server, and M2M client.
3. Create the AgentCore Gateway and wait for `READY` (built-in poll).
4. Create S3 bucket `agentcore-gateway-<account>-<region>` and upload `phantombuster-v2.json`.
5. Register an API_KEY credential provider holding your PhantomBuster key.
6. Create the gateway target.
7. Verify a Cognito M2M token can be retrieved (retries up to 5× while the Cognito domain propagates).

When successful, prints:
```
Gateway URL:  https://phantombuster-api-gateway-<random>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
Gateway ID:   phantombuster-api-gateway-<random>
Target Name:  phantombuster-poc-target
S3 Spec URI:  s3://agentcore-gateway-<account>-us-east-1/phantombuster-v2.json
```

Now retrieve credentials and test:
```bash
python get_credentials.py     # writes credentials.env (chmod 600) with GATEWAY_URL/CLIENT_ID/CLIENT_SECRET/TOKEN_URL
python test_gateway.py        # 7/7 should pass — direct API calls + gateway round-trip
```

Common failures and fixes:
- **`Caller ARN ... is not an IAM user`** — you're using SSO. Generate a long-lived access key for an IAM user and put it in `.env`.
- **`Cannot perform operation CreateGatewayTarget when gateway is in CREATING status`** — already handled by `wait_for_gateway_ready`. If you somehow hit this, just re-run the script (idempotent).
- **`AccessDeniedException` on `GetCredentialProvider`** — your IAM user lacks `bedrock-agentcore-control:*`. Update the policy from §1.1.
- **`Token retrieval failed`** — Cognito hosted-domain DNS hasn't propagated. Wait 60s, re-run `python get_credentials.py`. The setup script retries 5× automatically.

---

## 4. Configure PhantomBuster Phantom

The agent is wire-compatible with any Sales-Nav-Search-style Phantom but for the demo to actually scrape leads:

1. In PhantomBuster UI, open the Phantom and go to **Settings → Launch**.
2. Switch from **Manually** to **Repeatedly** (any cadence — schedule itself doesn't matter; this is what unlocks API launches).
3. Save.
4. Note the agent ID (16-digit number in the URL after `/`).

Optional sanity check — runs a real scrape (consumes ~1 minute of execution time):
```bash
cd phantombuster-api
python query_user.py --company "Republic Services" --state NJ --agent-id <your-agent-id>
```
Expect a list of LinkedIn leads matching the title filter list.

---

## 5. Deploy the outreach agent to AgentCore Runtime

### 5.1 Sync gateway credentials into the agent's `.env`
The agent reads the gateway URL and Cognito client info from its own `.env`. After §3 you have these in `phantombuster-api/credentials.env`. Copy them over:

```bash
cd reworld-outreach-agent
{
  cat ../phantombuster-api/credentials.env
  echo 'SCOPE="phantombuster-api-gateway-id/gateway:read phantombuster-api-gateway-id/gateway:write"'
} >> .env
```

(The `RESOURCE_SERVER_ID` portion of `SCOPE` must match `phantombuster-api/.env`. If you renamed it, adjust accordingly.)

### 5.2 Local sanity check (no AWS deploy yet)
```bash
pip install -r requirements.txt
echo '{"full_name":"Tim K","organization_name":"Republic Services","state":"NJ","industryName":"Environmental Services"}' | python agent.py
```
Expect strict JSON `{"email": "..."}` — agent → Bedrock → Cognito → Gateway works end-to-end before you deploy.

### 5.3 Create the runtime IAM role
The runtime container assumes this role to call Bedrock and read Secrets Manager.

Run the helper script (idempotent — re-runs reuse the role and refresh the inline policy):

```bash
cd reworld-outreach-agent
python create_runtime_role.py
```

The script resolves the active AWS account via STS (no `AWS_ACCOUNT_ID` required in `.env`), creates `reworld-outreach-agent-runtime` if it doesn't exist, attaches the inline `agent-runtime-permissions` policy, and waits 8s for IAM propagation.

A `--dry-run` flag prints the full policy JSON without applying it; `--region` and `--role-name` overrides are also available.

### 5.4 Build, push, and register the runtime

```bash
python runtime_deploy.py --create-ecr
```

What it does:
1. Creates ECR repository `reworld-outreach-agent`.
2. `docker buildx --platform linux/arm64 --load` builds the image. **AgentCore Runtime today accepts only linux/arm64.** Build time:
   - Apple Silicon dev box: native, ~30s.
   - Windows / Linux-x86 dev box: QEMU emulation, ~3-5 min. **One-time setup** (registers QEMU binfmt handlers in Docker Desktop's Linux VM):
     ```bash
     docker run --privileged --rm tonistiigi/binfmt --install all
     ```
     After that, every subsequent `docker buildx build --platform linux/arm64 ...` works without changes.
3. Pulls an ECR auth token via boto3 (no `aws` CLI dependency) and `docker login`s.
4. Pushes `:latest`.
5. Calls `bedrock-agentcore-control.create_agent_runtime` with the right shape (`roleArn`, `networkConfiguration`, `protocolConfiguration={serverProtocol:"HTTP"}`, and `environmentVariables` are all top-level — not nested under `containerConfiguration`).

When successful, prints the runtime ARN, ID (e.g. `reworld_outreach_agent-XXXXXXX`), and `status: CREATING`.

Wait until `READY`:
```bash
python wait_for_runtime_ready.py
```
The script polls `get_agent_runtime` every 5s, exits 0 on `READY` (printing the ARN you can paste into `.env` as `AGENT_RUNTIME_ARN`), exits 1 on `CREATE_FAILED` / `UPDATE_FAILED` / timeout. Defaults: `--timeout 300`, `--poll-every 5`, `--runtime-name reworld_outreach_agent`.

### 5.5 Invoke and verify
```bash
echo '{"full_name":"Tim K","organization_name":"Republic Services","state":"NJ","industryName":"Environmental Services"}' \
    | python invoke_runtime.py --pretty
```
The script reads JSON from stdin (matching `agent.py`'s interface — same payload pipes work), resolves the runtime ARN from `AGENT_RUNTIME_ARN` in `.env` (or by name via the control plane), and prints the runtime's response body to stdout.

Flags: `--runtime-arn` (overrides env+name lookup), `--runtime-name`, `--region`, `--pretty`.

Expect `{"email": "..."}` end-to-end, ~5-7s.

---

## 6. Resource summary (per account)

After a successful deploy, the following exist in your account in `us-east-1`:

| Type | Name |
|---|---|
| IAM role (gateway) | `agentcore-phantombuster-api-gateway-role` |
| IAM role (runtime) | `reworld-outreach-agent-runtime` |
| Cognito user pool | `phantombuster-api-gateway-pool` |
| Cognito M2M client | `phantombuster-api-gateway-client` |
| AgentCore Gateway | `phantombuster-api-gateway` |
| Gateway target | `phantombuster-poc-target` |
| Credential provider | `phantombuster-api-api-key` |
| S3 bucket | `agentcore-gateway-<account>-us-east-1` |
| ECR repository | `reworld-outreach-agent` |
| AgentCore Runtime | `reworld_outreach_agent` |
| CloudWatch group | `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` |

Estimated steady-state cost: a few cents/month for idle resources; gateway invocations and runtime invocations are usage-priced (Cognito, Bedrock, AgentCore Runtime). The bulk of cost is Bedrock model invocation (Sonnet 4.6, ~$0.003 per email at this prompt size).

---

## 7. Cleanup / teardown

To delete everything (run from each project root with their `.env` loaded):

```bash
# Delete runtime
python3 -c "
import boto3, os
from dotenv import dotenv_values
for k,v in dotenv_values('reworld-outreach-agent/.env').items():
    if v: os.environ[k]=v
c = boto3.client('bedrock-agentcore-control', region_name='us-east-1')
for rt in c.list_agent_runtimes()['agentRuntimes']:
    if rt['agentRuntimeName'] == 'reworld_outreach_agent':
        c.delete_agent_runtime(agentRuntimeId=rt['agentRuntimeId'])
        print(f\"deleted runtime {rt['agentRuntimeId']}\")
"

# Delete gateway target, gateway, credential provider, Cognito pool, IAM roles, ECR repo, S3 bucket
# (no automated teardown script — delete in this order via console or CLI to avoid dependency errors)
```

Order matters: gateway target → gateway → credential provider → Cognito pool → IAM roles → ECR repo → S3 bucket (empty first).

---

## 8. Things that change per account vs. stay the same

**Per-account (must update):**
- AWS account ID
- IAM user access keys
- PhantomBuster workspace API key (per workspace, not per account)
- PhantomBuster Phantom agent ID (per workspace)
- Random suffixes baked into AWS-generated identifiers (gateway URL, runtime ID, Cognito pool ID)

**Constant across accounts (don't change):**
- PhantomBuster API base URL (`https://api.phantombuster.com/api/v2`)
- PhantomBuster auth header name (`X-Phantombuster-Key`)
- OpenAPI spec (`phantombuster-v2.json` — 8 operations)
- Cognito scope strings (`<resource-server-id>/gateway:read|write`)
- Tool prefix (`phantombuster-poc-target___`)
- AgentCore Runtime container contract (port 8080, POST `/invocations`, GET `/ping`)
- Image platform (linux/arm64)
- Email template (`templates.py`)

**Configurable but defaults are sensible:**
- Region — change `AWS_REGION` in both `.env`s and update inference-profile prefix accordingly.
- Bedrock model — `BEDROCK_MODEL_ID` in `reworld-outreach-agent/.env`. Any Anthropic model on the cross-region profile works; latency scales with model size.
- Resource names — change `*_NAME` variables in `phantombuster-api/.env` and `RUNTIME_NAME`/`IAM_ROLE_NAME` in `runtime_deploy.py` if name collisions exist.
