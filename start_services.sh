#!/bin/bash
# start_services.sh - Arranca Hermes y el Bot Discord en el mismo contenedor

set -e

echo "[1/2] Iniciando Hermes Agent en puerto 9119..."
# Hermes serve en background
hermes serve &
HERMES_PID=$!

# Esperar a que Hermes esté listo (health check)
echo "Esperando a Hermes..."
for i in {1..30}; do
    if curl -sf http://localhost:9119/health >/dev/null 2>&1 || curl -sf http://localhost:9119/v1/models >/dev/null 2>&1; then
        echo "Hermes listo en http://localhost:9119"
        break
    fi
    sleep 1
done

echo "[2/2] Iniciando Discord Bot..."
# Bot en foreground (proceso principal del contenedor)
exec python discord_hermes_bot.py