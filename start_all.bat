@echo off
REM Inicio automático: Hermes + Discord Bot
REM Coloca este archivo en shell:startup para auto-arranque al encender PC

cd /d "%~dp0"

echo [1/2] Iniciando Hermes Agent en puerto 9119...
start "Hermes Agent" cmd /k hermes serve

REM Esperar a que Hermes esté listo
timeout /t 8 >nul

echo [2/2] Iniciando Discord Bot...
start "Discord Bot" cmd /k python discord_hermes_bot.py

echo.
echo Ambos servicios iniciados en ventanas separadas.
echo Cierra esta ventana cuando quieras (los otros siguen corriendo).
pause