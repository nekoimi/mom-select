FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

RUN uv run playwright install --with-deps chromium

COPY mom_select ./mom_select
COPY scripts ./scripts
COPY main.py README.md ./
COPY config/etf_pool.csv ./config/etf_pool.csv
COPY config/config.example.yaml ./config/config.example.yaml

RUN uv sync --frozen --no-dev

RUN mkdir -p /app/config /app/data/cache /app/data/state /app/reports \
    && chown -R 10001:10001 /app /ms-playwright

USER 10001:10001

ENTRYPOINT ["/app/.venv/bin/python", "-m", "scripts.mom_select_scheduler"]
CMD ["--config", "/app/config/config.yaml"]
