# syntax=docker/dockerfile:1.7
# FibreOps NOC console — Azure Container Apps image
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt pyproject.toml ./
COPY src ./src

# Install runtime deps + the package itself (gives us the `fibreops` entry-point)
RUN pip install --upgrade pip \
 && pip install -r requirements.txt \
 && pip install --no-deps .

# --- Runtime image ---
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FIBREOPS_UI_HOST=0.0.0.0 \
    FIBREOPS_UI_PORT=8800

# Non-root user so the container app honours the rootless ACA default
RUN groupadd --system fibreops \
 && useradd  --system --create-home --gid fibreops fibreops

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# state/ is bind-mounted in dev; in the container it is ephemeral on /tmp
RUN mkdir -p /app/state \
 && chown -R fibreops:fibreops /app

USER fibreops

# Bake the published Foundry agent registry into the image so the
# hosted backend can bind immediately at cold start. The file is
# generated locally via `python -m fibreops.demo publish` and lists
# {role: {agent_name, version}} for the three hosted Prompt Agents.
COPY --chown=fibreops:fibreops state/foundry_agents.json /app/state/foundry_agents.json

EXPOSE 8800

# Readiness comes from the FastAPI /healthz endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/healthz', timeout=3).status==200 else 1)"

CMD ["python", "-m", "fibreops.demo", "ui", "--host", "0.0.0.0", "--port", "8800"]
