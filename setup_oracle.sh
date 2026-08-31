#!/bin/bash
# setup_oracle.sh - Instalación completa en Oracle Cloud Free Tier
# Ejecutar en la VM Ubuntu: curl -fsSL https://raw.githubusercontent.com/animusicl/membrillo-bot/main/setup_oracle.sh | bash

set -e

echo "=========================================="
echo "  Oracle Cloud Free Tier - Hermes Discord Bot"
echo "=========================================="

# 1. Actualiza e instala Docker
echo "[1/6] Instalando Docker..."
sudo apt-get update -qq
sudo apt-get install -y -qq docker.io docker-compose-plugin git curl
sudo usermod -aG docker $USER

# 2. Clona repo
echo "[2/6] Clonando repositorio..."
cd /home/ubuntu
if [ -d "membrillo-bot" ]; then
    cd membrillo-bot && git pull
else
    git clone https://github.com/animusicl/membrillo-bot.git
    cd membrillo-bot
fi

# 3. Crea .env si no existe
echo "[3/6] Configurando variables de entorno..."
if [ ! -f .env ]; then
    cat > .env << 'ENVEOF'
DISCORD_TOKEN=TU_TOKEN_AQUI
HERMES_MODEL=nemotron-3-ultra-free
MAX_HISTORY=500
HERMES_URL=http://hermes:9119
ENVEOF
    echo "⚠️  .env creado con placeholder. EDÍTALO: nano .env"
fi

# 4. Levanta contenedores
echo "[4/6] Construyendo y levantando contenedores..."
docker compose up -d --build

# 5. Verifica salud
echo "[5/6] Verificando servicios..."
sleep 10
for i in {1..30}; do
    if docker compose ps | grep -q "healthy"; then
        echo "✅ Servicios saludables"
        break
    fi
    sleep 2
done

# 6. Systemd service para auto-arranque
echo "[6/6] Configurando auto-arranque (systemd)..."
sudo tee /etc/systemd/system/hermes-bot.service > /dev/null << 'SVC_EOF'
[Unit]
Description=Hermes Discord Bot
Requires=docker.service
After=docker.service network.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ubuntu/membrillo-bot
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
SVC_EOF

sudo systemctl daemon-reload
sudo systemctl enable hermes-bot
sudo systemctl start hermes-bot

echo ""
echo "=========================================="
echo "  ✅ INSTALACIÓN COMPLETA"
echo "=========================================="
echo ""
echo "Bot corriendo en background. Comandos útiles:"
echo "  Ver logs:      docker compose logs -f"
echo "  Estado:        docker compose ps"
echo "  Reiniciar:     docker compose restart"
echo "  Parar:         docker compose down"
echo "  Editar .env:   nano .env && docker compose up -d --build"
echo ""
echo "Systemd (auto-arranque al reboot):"
echo "  Estado:  sudo systemctl status hermes-bot"
echo "  Logs:    sudo journalctl -u hermes-bot -f"
echo ""
echo "⚠️  IMPORTANTE: Edita .env con tu token real de Discord:"
echo "   nano .env"
echo "   # Cambia DISCORD_TOKEN=TU_TOKEN_AQUI por tu token real"
echo "   docker compose up -d --build"
echo ""