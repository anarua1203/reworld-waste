# ── Reworld Outreach Agent — AgentCore Runtime container ────────────────────
#
# AgentCore Runtime today accepts ONLY linux/arm64 (Graviton) images.
#
# Apple Silicon dev box → native, fast (~30s):
#   docker buildx build --platform linux/arm64 --load -t reworld-outreach-agent:latest .
#
# Windows / Linux-x86 dev box → QEMU emulation (~3-5 min). One-time setup:
#   docker run --privileged --rm tonistiigi/binfmt --install all
# Then the same buildx command above works.
#
# runtime_deploy.py reads BUILD_PLATFORM from env (defaults to linux/arm64).
#
# The CLIENT_SECRET must NOT be baked in — pass it at runtime:
#   docker run -e CLIENT_SECRET=<secret> \
#              -e AWS_ACCESS_KEY_ID=... \
#              -e AWS_SECRET_ACCESS_KEY=... \
#              -p 8080:8080 reworld-outreach-agent:latest
# ---------------------------------------------------------------------------

FROM python:3.12-slim

# AgentCore Runtime listens on 8080 by default
ENV PORT=8080
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (separate layer for caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY agent.py mcp_auth.py templates.py entrypoint.py ./

# .env.example is safe to copy (no real secrets)
COPY .env.example .

# The container does NOT embed a .env with real credentials.
# Secrets are injected via environment variables at runtime
# (AgentCore Runtime → Secrets Manager reference, or docker run -e ...).

EXPOSE 8080

# AgentCore Runtime expects an HTTP server on /invocations (POST) and /ping (GET).
# uvicorn serves the Starlette app from BedrockAgentCoreApp.
CMD ["uvicorn", "entrypoint:app", "--host", "0.0.0.0", "--port", "8080"]
