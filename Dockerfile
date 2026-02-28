FROM --platform=linux/amd64 debian:bookworm-slim

WORKDIR /app

# Install curl and ca-certificates for pixi installer
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install pixi
RUN curl -fsSL https://pixi.sh/install.sh | bash
ENV PATH="/root/.pixi/bin:${PATH}"

# Copy dependency files first (layer caching)
COPY pyproject.toml pixi.lock ./

# Install dependencies using lock file
RUN pixi install --locked

# Copy api_dock configuration
COPY api_dock_config/ api_dock_config/

EXPOSE 8080

CMD ["pixi", "run", "api_dock", "start", "--host", "0.0.0.0", "--port", "8080"]
