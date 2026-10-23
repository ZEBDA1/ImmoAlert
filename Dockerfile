FROM node:22-bookworm-slim AS node-runtime

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=UTF-8 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    # Keep npm global installs on PATH for the non-login shell used by CMD.
    NPM_CONFIG_PREFIX=/usr/local \
    NODE_PATH=/usr/local/lib/node_modules

WORKDIR /app

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Runtime deps:
#   ca-certificates/curl  - TLS + in-container diagnostics
#   chromium              - Selenium/Playwright legacy fallbacks
#   Node 22 + npm above   - required by the Firecrawl CLI (leboncoin/seloger)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    chromium \
    fonts-unifont \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Firecrawl CLI. leboncoin.fr and seloger.com answer plain HTTP requests with
# 403 (DataDome/Cloudflare), so those two sources are rendered through this
# CLI. Install it globally and verify it is reachable, failing the build early
# instead of silently losing two sources at runtime.
RUN npm install -g firecrawl-cli@1.26.22 && \
    firecrawl --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python scripts/healthcheck.py

CMD ["python", "main.py"]
