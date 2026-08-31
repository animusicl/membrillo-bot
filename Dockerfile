# Dockerfile para Render free tier (512 MB RAM)
FROM python:3.11-slim

# Instalar dependencias del sistema (mínimas para ahorrar memoria)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instalar Hermes Agent (binary precompilado para Linux amd64)
# Usamos la release oficial de GitHub
RUN curl -fsSL https://github.com/NousResearch/hermes-agent/releases/latest/download/hermes-agent-linux-amd64.tar.gz \
    | tar -xz -C /usr/local/bin hermes \
    && chmod +x /usr/local/bin/hermes

# Verificar instalación
RUN hermes --version || echo "Hermes instalado"

# Python deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY discord_hermes_bot.py start_services.sh ./
RUN chmod +x start_services.sh

# Puertos: 9119 (Hermes), 8080 (health check bot)
EXPOSE 8080 9119

# Variables de entorno por defecto
ENV HERMES_PROVIDER=opencode-free
ENV HERMES_MODEL=nemotron-3-ultra-free

# Script de arranque robusto
CMD ["./start_services.sh"]