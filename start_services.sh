#!/bin/bash
# start_services.sh - Arranca Hermes y el Bot Discord en el mismo contenedor
# Robusto para Render free tier (memoria limitada, startup variable)

set -e

echo "[1/3] Verificando instalación de Hermes..."
if ! command -v hermes &> /dev/null; then
    echo "❌ Hermes no encontrado en PATH"
    ls -la /usr/local/bin/ || true
    exit 1
fi
echo "✅ Hermes: $(hermes --version 2>/dev/null || echo 'versión desconocida')"

echo "[2/3] Iniciando Hermes Agent en puerto 9119..."
# Hermes serve en background con logs visibles
hermes serve > /tmp/hermes.log 2>&1 &
HERMES_PID=$!
echo "Hermes PID: $HERMES_PID"

# Esperar a que Hermes esté listo (health check con timeout)
echo "Esperando a Hermes..."
HERMES_READY=false
for i in {1..60}; do
    if curl -sf http://localhost:9119/health >/dev/null 2>&1 || \
       curl -sf http://localhost:9119/v1/models >/dev/null 2>&1 || \
       curl -sf http://localhost:9119/ >/dev/null 2>&1; then
        echo "✅ Hermes listo en http://localhost:9119"
        HERMES_READY=true
        break
    fi
    # Verificar si el proceso murió
    if ! kill -0 $HERMES_PID 2>/dev/null; then
        echo "❌ Hermes process murió. Logs:"
        cat /tmp/hermes.log
        exit 1
    fi
    sleep 1
done

if [ "$HERMES_READY" = false ]; then
    echo "⚠️ Timeout esperando a Hermes. Logs:"
    cat /tmp/hermes.log
    echo "Continuando sin Hermes (bot fallará en requests)..."
fi

echo "[3/3] Iniciando Discord Bot..."
# Bot en foreground (proceso principal del contenedor)
# Trap signals para cleanup limpio
trap 'kill $HERMES_PID 2>/dev/null; exit 0' SIGTERM SIGINT

exec python discord_hermes_bot.py