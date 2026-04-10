# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# ── Install system deps + Rust toolchain for Kraken CLI ───────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Rust (minimal) and build Kraken CLI from GitHub
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal \
    && . "$HOME/.cargo/env" \
    && cargo install --git https://github.com/krakenfx/kraken-cli --tag v0.3.0 \
    && cp "$HOME/.cargo/bin/kraken" /usr/local/bin/kraken \
    && rustup self uninstall -y \
    && rm -rf "$HOME/.cargo" \
    && kraken --version

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application ───────────────────────────────────────────────────────────────
COPY aria.py ./
COPY src/ ./src/
COPY templates/ ./templates/
RUN mkdir -p logs

# Expose dashboard port (Railway injects PORT env var automatically)
EXPOSE 8080

# Health check for container orchestrators
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/healthz || exit 1

# Initialise paper account at container start, then run the agent
CMD kraken futures paper init 2>/dev/null || true && python aria.py
