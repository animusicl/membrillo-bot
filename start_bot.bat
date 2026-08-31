@echo off
REM Inicio automático: Hermes + Discord Bot
REM Este archivo va en: shell:startup

cd /d "C:\Users\animu\hermes-discord-bot"

echo [1/2] Iniciando Hermes Agent (puerto 9119)...
start "Hermes Agent" cmd /k hermes serve

REM Esperar a que Hermes esté listo
timeout /t 10 >nul

echo [2/2] Iniciando Discord Bot...
start "Discord Bot" cmd /k python discord_hermes_bot.py

echo.
echo Servicios iniciados. Puedes cerrar esta ventana.
pause