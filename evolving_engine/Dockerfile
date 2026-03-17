FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (Docker CLI for sandbox)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Configure git identity for local commits (evolved-app repo)
RUN git config --global user.email "engine@self-evolving-software.local" \
    && git config --global user.name "Self-Evolving Engine"

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy engine code
COPY . .

# Default entrypoint
ENTRYPOINT ["python", "-m", "engine"]
