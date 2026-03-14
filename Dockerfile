FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY bot/ bot/
COPY scripts/ scripts/

RUN useradd -r -m -s /bin/false botuser
USER botuser

CMD ["uv", "run", "python", "-m", "bot.main"]
