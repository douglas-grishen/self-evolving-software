FROM python:3.11-slim

WORKDIR /app

# Install system dependencies:
#   - git: for local evolved-app commits and rollbacks
#   - curl + ca-certificates: for Docker CLI installation
#   - Docker CLI + Compose plugin: for sandbox builds and service rebuilds
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Configure git identity for local commits (evolved-app repo)
RUN git config --global user.email "engine@self-evolving-software.local" \
    && git config --global user.name "Self-Evolving Engine"

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .
RUN python -m playwright install --with-deps chromium

# Copy engine code
COPY . .

# Default entrypoint
ENTRYPOINT ["python", "-m", "engine"]
