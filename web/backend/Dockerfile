FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache

COPY dairy_web ./dairy_web

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["/app/.venv/bin/uvicorn", "dairy_web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
