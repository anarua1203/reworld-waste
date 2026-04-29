# phantombuster-api — AgentCore Gateway

Exposes the **PhantomBuster REST API v2** as MCP tools via Amazon Bedrock AgentCore Gateway. Part of the Reworld Waste Sales Acceleration Platform — Contact-Resolution agent.

The gateway fronts these PhantomBuster operations as MCP-callable tools:

| MCP Tool (operationId) | What it does |
|---|---|
| `fetchOrgInfo` | Verify API key; get workspace metadata |
| `fetchAllAgents` | List workspace Phantoms/Flows; discover agent IDs |
| `fetchAgent` | Get one agent's config, status, last-run result |
| `launchAgent` | Launch a Phantom (e.g. LinkedIn Profile Scraper) |
| `fetchAgentOutput` | Poll live console output while a Phantom runs |
| `fetchContainerOutput` | Fetch full result record + S3 result URL after run |
| `listContainers` | Browse recent runs for an agent |
| `saveAgentArgument` | Update a Phantom's saved argument before launch |

## Architecture

```
Agent → Cognito M2M token → AgentCore Gateway (CUSTOM_JWT)
                                       ↓
                            OpenAPI target (S3 spec)
                                       ↓
                    PhantomBuster REST API v2  [X-Phantombuster-Key header injected by credential provider]
```

## Setup

### 1. Prerequisites

```bash
pip install boto3 python-dotenv requests
```

### 2. Fill in your PhantomBuster API key

Edit `.env` and replace the placeholder on the `PHANTOMBUSTER_API_KEY` line:

```
PHANTOMBUSTER_API_KEY=<your-key-from-workspace-settings>
```

Get your key from: `https://phantombuster.com/<your-workspace-id>/workspace-settings#api-keys`

### 3. Run the setup script

```bash
python phantombuster_api_gateway_setup.py
```

This creates (idempotently, in order):
1. IAM role `agentcore-phantombuster-api-gateway-role`
2. Cognito user pool `phantombuster-api-gateway-pool` with M2M client
3. AgentCore Gateway `phantombuster-api-gateway` (MCP, CUSTOM_JWT authorizer)
4. S3 bucket `agentcore-gateway-<account-id>-<region>`, uploads `phantombuster-v2.json`
5. API-key credential provider `phantombuster-api-api-key` (HEADER location)
6. Gateway target `phantombuster-poc-target` wired to the S3 spec
7. Cognito token verification (retries up to 5x for domain propagation)

On success, `.setup-state.json` is written — the script uses it to detect config drift on subsequent runs.

### 4. Retrieve MCP client credentials

```bash
python get_credentials.py
```

Writes `credentials.env` (chmod 600) with:
- `GATEWAY_URL` — the AgentCore Gateway MCP endpoint
- `CLIENT_ID` — Cognito M2M client ID
- `CLIENT_SECRET` — Cognito M2M client secret (sensitive)
- `TOKEN_URL` — Cognito `/oauth2/token` endpoint

### 5. Use from an agent or MCP client

Pass `GATEWAY_URL`, `CLIENT_ID`, `CLIENT_SECRET`, and `TOKEN_URL` to your Strands agent or any MCP-aware client. The client exchanges `client_credentials` for a Cognito JWT and presents it as `Authorization: Bearer <token>` to the gateway.

## Cleanup

To tear down all provisioned resources:

1. Delete gateway target `phantombuster-poc-target` in Bedrock AgentCore console
2. Delete gateway `phantombuster-api-gateway`
3. Delete credential provider `phantombuster-api-api-key`
4. Delete Cognito user pool `phantombuster-api-gateway-pool`
5. Delete IAM role `agentcore-phantombuster-api-gateway-role`
6. Remove `phantombuster-v2.json` from S3 bucket `agentcore-gateway-<account-id>-<region>`
7. Delete `.setup-state.json` to reset the drift guard

## Security notes

- `.env` and `credentials.env` are gitignored — never commit them
- The PhantomBuster API key is stored in the AgentCore credential provider (encrypted at rest) and injected as the `X-Phantombuster-Key` header by the gateway — it never appears in agent tool-call payloads
- The Cognito JWT authorizer scopes (`gateway:read`, `gateway:write`) ensure only authenticated M2M clients reach the gateway
