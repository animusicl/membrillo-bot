# Single-stage Dockerfile for Discord Bot (Python 3.11 slim)
# ~100MB final image, no Hermes, no local models

FROM python:3.11-slim

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -u 1000 botuser
WORKDIR /app

# Copy requirements first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY discord_hermes_bot.py .

# Ownership
RUN chown -R botuser:botuser /app
USER botuser

# Render expects port 8080 for health checks
EXPOSE 8080

# Run bot
CMD ["python", "discord_hermes_bot.py"]