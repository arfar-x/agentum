FROM python:3.12-slim

WORKDIR /opt/agent-gateway
COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir '.[serve]'

ENV GATEWAY_CONFIG_DIR=/etc/agent-gateway \
    GATEWAY_MIGRATIONS_DIR=/opt/agent-gateway/migrations \
    GATEWAY_PORT=8080
EXPOSE 8080
USER nobody
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"GATEWAY_PORT\"]}/healthz')"
CMD ["python", "-m", "agent_gateway", "serve"]
