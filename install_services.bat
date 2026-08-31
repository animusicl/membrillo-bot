# Hermes Discord Bot - Instalación como Servicios Windows (NSSM)
# Más robusto: auto-restart, background, sobrevive a reinicios, logs centralizados

@echo off
REM ============================================
# REQUISITOS: NSSM instalado (scoop install nssm / choco install nssm)
# EJECUTAR COMO ADMINISTRADOR
# ============================================

set BOT_DIR=C:\Users\animu\hermes-discord-bot
set HERMES_EXE=C:\Users\animu\AppData\Local\Programs\Ollama\ollama.exe

echo [1/4] Creando servicio: Hermes Agent (Ollama)...
nssm install HermesAgent "%HERMES_EXE%" serve
nssm set HermesAgent AppDirectory "%BOT_DIR%"
nssm set HermesAgent Description "Hermes Agent API Server (Ollama backend)"
nssm set HermesAgent Start SERVICE_AUTO_START
nssm set HermesAgent AppStdout "%BOT_DIR%\logs\hermes.log"
nssm set HermesAgent AppStderr "%BOT_DIR%\logs\hermes.err.log"
nssm set HermesAgent AppRotateFiles 1
nssm set HermesAgent AppRotateOnline 1

echo [2/4] Creando servicio: Discord Bot...
nssm install HermesDiscordBot python.exe "%BOT_DIR%\discord_hermes_bot.py"
nssm set HermesDiscordBot AppDirectory "%BOT_DIR%"
nssm set HermesDiscordBot Description "Discord Bot para Hermes Agent (sesión compartida)"
nssm set HermesDiscordBot Start SERVICE_AUTO_START
nssm set HermesDiscordBot AppStdout "%BOT_DIR%\logs\bot.log"
nssm set HermesDiscordBot AppStderr "%BOT_DIR%\logs\bot.err.log"
nssm set HermesDiscordBot AppRotateFiles 1
nssm set HermesDiscordBot AppRotateOnline 1
nssm set HermesDiscordBot DependOnService HermesAgent

echo [3/4] Iniciando servicios...
net start HermesAgent
timeout /t 5 >nul
net start HermesDiscordBot

echo [4/4] Estado:
sc query HermesAgent
sc query HermesDiscordBot

echo.
echo LISTO. Servicios instalados y corriendo.
echo Logs en: %BOT_DIR%\logs\
echo.
echo Para ver logs: type %BOT_DIR%\logs\bot.log
echo Para reiniciar: net stop HermesDiscordBot && net start HermesDiscordBot
echo Para desinstalar: nssm remove HermesAgent confirm && nssm remove HermesDiscordBot confirm
pause