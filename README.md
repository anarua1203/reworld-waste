# Reworld Waste — AgentCore Gateway + Strands Outreach Agent

Reference implementation for an AWS Partner Engineering workshop: wrap a third-party REST API as MCP tools via Amazon Bedrock AgentCore Gateway, then deploy a Strands agent to AgentCore Runtime that consumes those tools and produces structured output.

## What's in here

```
reworld-waste/
├── DEPLOY.md                      ← step-by-step deploy runbook for a fresh AWS account
├── phantombuster-api/             ← AgentCore Gateway exposing PhantomBuster as 8 MCP tools
│   ├── phantombuster_api_gateway_setup.py    7-step idempotent provisioning script
│   ├── get_credentials.py                    fetches Cognito M2M client creds
│   ├── query_user.py                         CLI: build Sales-Nav URL + launch Phantom directly
│   ├── test_gateway.py                       direct + gateway end-to-end tests
│   ├── openapi-specs/phantombuster-v2.json   OpenAPI spec → 8 MCP operations
│   └── README.md
└── reworld-outreach-agent/        ← Strands agent → BedrockModel → MCPClient → Gateway
    ├── agent.py                              BedrockModel + MCPClient + render_email_tool
    ├── mcp_auth.py                           Cognito client_credentials + cached transport
    ├── templates.py                          Verbatim email template (no LLM rewrites)
    ├── entrypoint.py                         BedrockAgentCoreApp /invocations handler
    ├── runtime_deploy.py                     ECR build/push + create_agent_runtime
    ├── Dockerfile                            python:3.12-slim, port 8080, linux/arm64
    ├── tests/test_agent_local.py             16 tests (13 unit + 3 live)
    └── README.md
```

## Architecture

```
client (n8n / Lambda / another agent)
   │  bedrock-agentcore.invoke_agent_runtime
   ▼
AgentCore Runtime (Graviton container)
   │
   ▼
BedrockAgentCoreApp /invocations
   │
   ▼
Strands Agent ─── BedrockModel (Claude Sonnet 4.6 cross-region profile)
   │
   ├── render_email_tool (local, deterministic)
   │
   └── MCPClient
        │  Cognito client_credentials → JWT
        ▼
   AgentCore Gateway
        │  injects X-Phantombuster-Key header
        ▼
   PhantomBuster REST API
```

## Deploying

See [`DEPLOY.md`](./DEPLOY.md) for the full per-account runbook. High-level:

1. Enable Bedrock model access for Claude Sonnet 4.6 in your account
2. Create an IAM user with long-lived access keys (AgentCore rejects SSO)
3. Configure `phantombuster-api/.env` and `reworld-outreach-agent/.env` from the `.env.example` templates
4. `cd phantombuster-api && python phantombuster_api_gateway_setup.py && python get_credentials.py && python test_gateway.py`
5. `cd reworld-outreach-agent && python runtime_deploy.py --create-ecr`

End-to-end provisioning takes 15-25 min on a fresh account.

## Running the agent locally

```bash
cd reworld-outreach-agent
pip install -r requirements.txt
echo '{"full_name":"Tim K","organization_name":"Republic Services","state":"NJ","industryName":"Environmental Services"}' | python agent.py
```

Returns strict JSON `{"email": "..."}` end-to-end through the deployed gateway.

## Invoking the deployed runtime

```python
import boto3, json
data = boto3.client("bedrock-agentcore", region_name="us-east-1")
resp = data.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-east-1:<account>:runtime/<runtime-id>",
    payload=json.dumps({
        "full_name": "Tim K",
        "organization_name": "Republic Services",
        "state": "NJ",
        "industryName": "Environmental Services",
    }).encode(),
    contentType="application/json", accept="application/json",
)
print(resp["response"].read().decode())
```

## Testing

```bash
# Gateway: 7 tests covering direct API + gateway round-trip
cd phantombuster-api && python test_gateway.py

# Agent: 16 tests (13 unit + 3 live against the deployed gateway)
cd reworld-outreach-agent && python -m pytest tests/ -v
```

## Security notes

- `.env` and `credentials.env` are gitignored — never commit.
- The PhantomBuster API key is stored inside an AgentCore credential provider (encrypted at rest) and injected as the `X-Phantombuster-Key` header by the gateway. It never appears in agent tool-call payloads.
- The Cognito `CLIENT_SECRET` is currently passed to the runtime as a plain env var. Move it to AWS Secrets Manager before production — the runtime IAM role already grants `secretsmanager:GetSecretValue` on `arn:aws:secretsmanager:*:*:secret:reworld/outreach-agent/*`.
- AgentCore Runtime requires long-lived IAM user credentials for provisioning. The runtime container itself uses an IAM execution role (no long-lived creds needed at request time).

## License

MIT — see [LICENSE](./LICENSE).
