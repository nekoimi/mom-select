FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

RUN uv run --no-project --no-dev playwright install --with-deps chromium

COPY mom_select ./mom_select
COPY scripts ./scripts
COPY main.py ./
COPY config/etf_pool.csv ./config/etf_pool.csv
COPY config/config.example.yaml ./config/config.example.yaml

RUN uv sync --frozen --no-dev

RUN mkdir -p /app/config /app/data/cache /app/data/state /app/reports

ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "--no-sync", "python", "-m", "scripts.mom_select_scheduler"]
CMD ["--config", "/app/config/config.yaml"]
