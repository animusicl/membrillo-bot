# Discord Bot + OpenRouter (Modelos Gratuitos)

Bot de Discord que usa **OpenRouter** para acceder a modelos gratuitos (nemotron-3-ultra-free, etc.) sin infraestructura local.

## Arquitectura

```
Usuario Discord → Bot en Render (Docker) → OpenRouter API → Modelo gratuito
                    ↑
              session.json (historial compartido por canal)
```

- **Un solo contenedor** (~100 MB)
- **Sin Hermes local**, sin Tailscale, sin PC encendido
- **Sesión compartida**: todos los usuarios en un canal comparten historial
- **Rate limit por usuario** (30s) para proteger cuota gratuita
- **Health check** en `/health` (puerto 8080)

## Variables de entorno (Render → Environment)

| Variable | Valor | Secreto |
|----------|-------|---------|
| `DISCORD_TOKEN` | Tu token de Discord Developer Portal | ✅ Sí |
| `OPENROUTER_API_KEY` | Tu key de OpenRouter | ✅ Sí |
| `HERMES_MODEL` | `openrouter/free` (o `openrouter/auto`) | No |
| `MAX_HISTORY` | `500` | No |

> **Nota**: Render Free puede suspender el servicio tras 15 min de inactividad. Para 24/7 real necesitas plan pago o VPS.

## Despliegue en Render

1. **Fork/Clona** este repo
2. **Render Dashboard** → New → Web Service → Connect repo
3. **Settings**:
   - Runtime: `Docker`
   - Dockerfile Path: `./Dockerfile`
   - Plan: `Free`
   - Health Check Path: `/health`
4. **Environment Variables**: añade las 4 variables de arriba (marca `DISCORD_TOKEN` y `OPENROUTER_API_KEY` como secretos)
5. **Create Web Service** → espera build + deploy

## Desarrollo local

```bash
# 1. Clona
git clone https://github.com/animusicl/membrillo-bot.git
cd membrillo-bot

# 2. Variables
cp .env.example .env  # edita con tus tokens

# 3. Dependencias
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 4. Ejecuta
python discord_hermes_bot.py
```

## Comandos Slash

| Comando | Descripción |
|---------|-------------|
| `/chat <mensaje>` | Habla con el bot |
| `/reset` | Borra historial del canal |
| `/history` | Muestra mensajes en contexto |
| `/status` | Estado del bot y sesión |
| `/config` | Configuración actual |

También responde a **menciones @bot** y **DMs**.

## Estructura

```
├── discord_hermes_bot.py   # Bot principal (discord.py + aiohttp)
├── requirements.txt        # Dependencias Python
├── Dockerfile              # Imagen single-stage
├── .dockerignore           # Excluye archivos locales
├── render.yaml             # Config Render (IaC)
├── session.json            # Historial persistido (se crea solo)
└── README.md               # Este archivo
```

## Rate Limits y Cuotas

- **OpenRouter Free**: ~50 requests/día sin créditos
- **Bot cooldown**: 30s por usuario (configurable `USER_COOLDOWN_SECONDS`)
- **429 handling**: mensaje amable, sin exponer detalles
- **Timeouts**: 30s request, 120s stream

## Logs

Solo eventos operativos:
- Inicio, modelo seleccionado
- Latencia por respuesta
- Errores y rate limits
- **Nunca**: prompts, respuestas completas, tokens, API keys, IDs personales

## Licencia

MIT