FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Run migrations then start uvicorn
CMD ["./entrypoint.sh"]

# ---------- Test stage (used by the evolving-engine sandbox) ----------
FROM base AS test

# Install dev/test dependencies (pytest, ruff, etc.)
RUN pip install --no-cache-dir ".[dev]"

CMD ["pytest", "tests/", "-v", "--tb=short"]
