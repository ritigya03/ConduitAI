FROM python:3.12-slim
RUN pip install --no-cache-dir uv
WORKDIR /code
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev
COPY app ./app
EXPOSE 8100
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
