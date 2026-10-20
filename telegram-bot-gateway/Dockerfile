FROM python:3.12-slim

WORKDIR /opt/telegram-bot-gateway
COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir '.[serve]'

ENV MIGRATIONS_DIR=/opt/telegram-bot-gateway/migrations \
    HEALTH_FILE=/tmp/healthy
USER nobody
# The bot publishes no port; it writes a heartbeat file while it is polling.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=40s \
  CMD python -c "import os,sys,time;sys.exit(0 if time.time()-os.path.getmtime(os.environ['HEALTH_FILE'])<90 else 1)"
CMD ["python", "-m", "telegram_bot_gateway", "serve"]
