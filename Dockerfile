# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# ── Install system deps + Kraken CLI v0.3.0 (Linux amd64) ────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL \
    "https://github.com/nicholasgasior/gsfmt/releases/download/v0.3.0/kraken_linux_amd64" \
    -o /usr/local/bin/kraken \
    && chmod +x /usr/local/bin/kraken \
    && kraken --version

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application ───────────────────────────────────────────────────────────────
COPY *.py ./
RUN mkdir -p logs

# Expose dashboard port (Railway injects PORT env var automatically)
EXPOSE 8080

# Initialise paper account at container start, then run the agent
CMD kraken paper init 2>/dev/null || true && python aria.py
