# Dockerfile multi-stage: Hermes + Bot en una imagen
FROM python:3.11-slim AS base

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar Hermes CLI (precompilado)
RUN curl -fsSL https://get.hermes-agent.com | sh -s -- -b /usr/local/bin

# Copiar requirements e instalar deps Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código del bot
COPY discord_hermes_bot.py .
COPY start_services.sh .

# Puerto de Hermes (interno) y health check del bot
EXPOSE 9119

# Script de arranque: levanta Hermes y luego el bot
CMD ["/app/start_services.sh"]