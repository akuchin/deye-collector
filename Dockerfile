FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY collector.py registers.py ./

CMD [".venv/bin/python", "collector.py"]
