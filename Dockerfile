FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=.venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 dairybot \
    && useradd --uid 1000 --gid 1000 --home-dir /tmp --no-create-home --shell /bin/sh dairybot

WORKDIR /app
RUN mkdir -p /app/data && chown -R dairybot:dairybot /app/data

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-install-project

COPY src ./src
COPY README.md .

RUN uv sync --frozen --no-cache

ENV PATH="/app/.venv/bin:${PATH}"

# Разрешаем git работать с примонтированной директорией /data, даже если у неё другой владелец
RUN git config --global --add safe.directory /data

CMD ["python", "src/bot.py"]
