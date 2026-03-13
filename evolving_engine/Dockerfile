FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (Docker CLI for sandbox)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy engine code
COPY . .

# Default entrypoint
ENTRYPOINT ["python", "-m", "engine"]
