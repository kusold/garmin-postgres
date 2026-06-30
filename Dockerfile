FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PATH="/app/.venv/bin:${PATH}" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY alembic.ini ./alembic.ini
COPY apps ./apps
COPY packages ./packages
COPY prefect.yaml ./prefect.yaml

RUN uv sync --locked --all-packages --no-dev

CMD ["prefect", "flow-run", "execute"]
