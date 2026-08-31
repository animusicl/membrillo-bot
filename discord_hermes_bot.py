#!/usr/bin/env python3
"""
Discord Bot + Hermes Agent (sesión compartida por canal)
- Guarda historial en session.json por canal
- Límite configurable MAX_HISTORY
- Comandos slash + menciones @bot
- Streaming de respuestas
- Arquitectura modular y escalable
"""

import os
import json
import asyncio
import logging
from collections import deque
from pathlib import Path
from typing import Dict, Deque, List, Optional, Any
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp

# ─── Configuración centralizada ───
load_dotenv()

@dataclass(frozen=True)
class Config:
    DISCORD_TOKEN: str
    HERMES_MODEL: str = "nemotron-3-ultra-free"
    MAX_HISTORY: int = 500
    HERMES_URL: str = "http://localhost:9119"
    SESSION_FILE: Path = Path("session.json")
    LOG_LEVEL: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("Falta DISCORD_TOKEN en .env")
        return cls(
            DISCORD_TOKEN=token,
            HERMES_MODEL=os.getenv("HERMES_MODEL", "nemotron-3-ultra-free"),
            MAX_HISTORY=int(os.getenv("MAX_HISTORY", "500")),
            HERMES_URL=os.getenv("HERMES_URL", "http://localhost:9119"),
        )

CFG = Config.from_env()

# ─── Logging estructurado ───
logging.basicConfig(
    level=getattr(logging, CFG.LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("hermes-discord-bot")

# ─── Capa de persistencia (sesión compartida) ───
class SharedSession:
    """Sesión compartida por canal con persistencia JSON atómica."""
    
    def __init__(self, max_history: int, session_file: Path):
        self.max_history = max_history
        self.session_file = session_file
        self._data: Dict[str, Deque[dict]] = {}
        self._load()

    def _key(self, channel_id: int) -> str:
        return str(channel_id)

    def _load(self) -> None:
        if self.session_file.exists():
            try:
                raw = json.loads(self.session_file.read_text(encoding="utf-8"))
                self._data = {k: deque(v, maxlen=self.max_history) for k, v in raw.items()}
                log.info(f"Sesión cargada: {len(self._data)} canales, max_history={self.max_history}")
            except Exception as e:
                log.warning(f"No se pudo cargar {self.session_file}: {e}")
                self._data = {}

    def _save(self) -> None:
        tmp = self.session_file.with_suffix(".tmp")
        try:
            serializable = {k: list(v) for k, v in self._data.items()}
            tmp.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.session_file)  # atómico en POSIX/Windows
        except Exception as e:
            log.error(f"Error guardando sesión: {e}")
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def add(self, channel_id: int, role: str, content: str, author: str = "") -> None:
        key = self._key(channel_id)
        if key not in self._data:
            self._data[key] = deque(maxlen=self.max_history)
        self._data[key].append({"role": role, "content": content, "author": author})
        self._save()

    def get_history(self, channel_id: int) -> List[dict]:
        return list(self._data.get(self._key(channel_id), []))

    def clear(self, channel_id: int) -> None:
        key = self._key(channel_id)
        if key in self._data:
            self._data[key].clear()
            self._save()

    def stats(self) -> dict:
        return {
            "canales": len(self._data),
            "total_mensajes": sum(len(v) for v in self._data.values()),
            "max_history": self.max_history,
            "archivo": str(self.session_file.absolute()),
        }

session = SharedSession(CFG.MAX_HISTORY, CFG.SESSION_FILE)

# ─── Cliente Hermes (API local) ───
class HermesClient:
    """Cliente asíncrono para Hermes Agent API (OpenAI-compatible)."""
    
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._session: Optional[aiohttp.ClientSession] = None

    @asynccontextmanager
    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        yield self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def chat_stream(self, messages: List[dict]) -> Any:
        """Genera chunks de respuesta desde Hermes /v1/chat/completions."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 4000,
        }
        url = f"{self.base_url}/v1/chat/completions"

        async with self._get_session() as sess:
            try:
                async with sess.post(url, json=payload) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Hermes HTTP {resp.status}: {text[:200]}")

                    async for line in resp.content:
                        line = line.decode("utf-8").strip()
                        if not line or line == "data: [DONE]":
                            continue
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue

            except aiohttp.ClientConnectorError:
                raise RuntimeError(
                    f"No se puede conectar a Hermes en {self.base_url}. "
                    "¿Está corriendo? (hermes serve)"
                )
            except Exception as e:
                raise RuntimeError(f"Error Hermes: {e}")

hermes = HermesClient(CFG.HERMES_URL, CFG.HERMES_MODEL)

# ─── Utilidades de formato ───
SYSTEM_PROMPT = (
    "Eres un asistente útil en Discord. "
    "Responde en español, sé conciso, directo y útil. "
    "Mantén el contexto de la conversación compartida."
)

def build_messages(history: List[dict]) -> List[dict]:
    """Construye la lista de mensajes para Hermes con system prompt."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs.extend({"role": m["role"], "content": m["content"]} for m in history)
    return msgs

# ─── Bot Discord ───
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    log.info(f"Conectado como {bot.user} (ID: {bot.user.id})")
    log.info(f"Servers: {[g.name for g in bot.guilds]}")
    try:
        synced = await bot.tree.sync()
        log.info(f"Slash commands sincronizados: {len(synced)}")
    except Exception as e:
        log.error(f"Error sincronizando comandos: {e}")
    
    # Iniciar health check server en EL MISMO loop del bot
    bot.loop.create_task(start_health_server())

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Mención @bot o DM → respuesta con Hermes
    if bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        await handle_hermes_message(message)
        return

    # Guardar mensaje de usuario en historial (contexto pasivo)
    session.add(message.channel.id, "user", message.content, str(message.author))

    await bot.process_commands(message)

async def handle_hermes_message(message: discord.Message):
    """Procesa mención @bot o DM y responde con streaming."""
    channel = message.channel
    # Limpiar mención del bot
    user_msg = (
        message.content
        .replace(f"<@!{bot.user.id}>", "")
        .replace(f"<@{bot.user.id}>", "")
        .strip()
    )

    if not user_msg:
        await channel.send("👋 ¡Hola! Mencióname o escríbeme por DM para charlar.")
        return

    # Guardar mensaje usuario
    session.add(channel.id, "user", user_msg, str(message.author))

    # Preparar historial
    history = session.get_history(channel.id)
    messages = build_messages(history)

    # Streaming response
    async with channel.typing():
        try:
            response_chunks = []
            reply_msg = None

            async for chunk in hermes.chat_stream(messages):
                response_chunks.append(chunk)
                full = "".join(response_chunks)

                if reply_msg is None:
                    reply_msg = await channel.send(full + "▌")
                elif len(full) % 50 == 0:
                    try:
                        await reply_msg.edit(content=full + "▌")
                    except (discord.NotFound, discord.HTTPException):
                        reply_msg = await channel.send(full + "▌")

            final_text = "".join(response_chunks).strip()
            if reply_msg:
                await reply_msg.edit(content=final_text)
            else:
                await channel.send(final_text)

            # Guardar respuesta del bot
            session.add(channel.id, "assistant", final_text, bot.user.name)

        except RuntimeError as e:
            await channel.send(f"❌ {e}")
            log.error(f"Error Hermes: {e}")
        except Exception as e:
            await channel.send(f"❌ Error inesperado: {e}")
            log.exception("Error en handle_hermes_message")

# ─── Slash Commands ───
@bot.tree.command(name="chat", description="Habla con Hermes (igual que mencionar al bot)")
@app_commands.describe(mensaje="Qué quieres decirle")
async def slash_chat(interaction: discord.Interaction, mensaje: str):
    await interaction.response.defer(thinking=True)
    class FakeMessage:
        def __init__(self, content, channel, author):
            self.content = content
            self.channel = channel
            self.author = author
    fake = FakeMessage(mensaje, interaction.channel, interaction.user)
    await handle_hermes_message(fake)

@bot.tree.command(name="reset", description="Borra el historial de este canal")
async def slash_reset(interaction: discord.Interaction):
    session.clear(interaction.channel.id)
    await interaction.response.send_message("🗑️ Historial de este canal borrado.", ephemeral=True)

@bot.tree.command(name="history", description="Muestra cuántos mensajes hay en el historial de este canal")
async def slash_history(interaction: discord.Interaction):
    count = len(session.get_history(interaction.channel.id))
    await interaction.response.send_message(
        f"📜 Mensajes en contexto: **{count}/{CFG.MAX_HISTORY}**", 
        ephemeral=True
    )

@bot.tree.command(name="status", description="Estado del bot y sesión")
async def slash_status(interaction: discord.Interaction):
    stats = session.stats()
    await interaction.response.send_message(
        f"🤖 **Bot:** {bot.user.name}\n"
        f"🧠 **Modelo:** {CFG.HERMES_MODEL}\n"
        f"🌐 **Hermes:** {CFG.HERMES_URL}\n"
        f"📊 **Canales con historial:** {stats['canales']}\n"
        f"💬 **Total mensajes:** {stats['total_mensajes']}\n"
        f"⚙️ **Max history:** {stats['max_history']}\n"
        f"💾 **Archivo:** `{stats['archivo']}`",
        ephemeral=True
    )

@bot.tree.command(name="config", description="Muestra configuración actual")
async def slash_config(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"⚙️ **Configuración:**\n"
        f"• Modelo: `{CFG.HERMES_MODEL}`\n"
        f"• Hermes URL: `{CFG.HERMES_URL}`\n"
        f"• Max history: `{CFG.MAX_HISTORY}`\n"
        f"• Session file: `{CFG.SESSION_FILE}`",
        ephemeral=True
    )

# ─── Lifecycle ───
async def shutdown():
    await hermes.close()
    log.info("Recursos liberados")

# ─── Health check server (para Koyeb/Railway) ───
from aiohttp import web

async def health_check(request):
    return web.json_response({"status": "ok", "bot": str(bot.user) if bot.user else "starting"})

async def start_health_server():
    app = web.Application()
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    log.info("Health check server en puerto 8080")

if __name__ == "__main__":
    log.info("Iniciando Hermes Discord Bot...")
    log.info(f"Hermes URL: {CFG.HERMES_URL}")
    log.info(f"Modelo: {CFG.HERMES_MODEL}")
    log.info(f"Max history: {CFG.MAX_HISTORY}")

    try:
        bot.run(CFG.DISCORD_TOKEN)
    except KeyboardInterrupt:
        log.info("Bot detenido por usuario")
    except Exception as e:
        log.exception(f"Error fatal: {e}")
    finally:
        # Nota: bot.run() bloquea, el cleanup real ocurre en signal handler si hace falta
        pass