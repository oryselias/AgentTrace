FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY configs ./configs
COPY evals ./evals
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8080

# Deterministic fake provider — no paid API key required.
CMD ["uvicorn", "control_plane.api:app", "--host", "0.0.0.0", "--port", "8080"]
