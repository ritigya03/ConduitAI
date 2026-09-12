FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /code

COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY seed ./seed
COPY docker-entrypoint.sh ./docker-entrypoint.sh

RUN uv sync && chmod +x docker-entrypoint.sh

ENV PATH="/code/.venv/bin:$PATH"

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
