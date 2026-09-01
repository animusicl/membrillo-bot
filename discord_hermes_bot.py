#!/usr/bin/env python3
"""
Discord Bot + OpenRouter (modelos gratuitos)
- Sesión compartida por canal (session.json)
- Límite configurable MAX_HISTORY
- Comandos slash + menciones @bot
- Streaming de respuestas
- Rate limit por usuario + manejo 429
- Arquitectura simple, un solo contenedor
"""

import os
import json
import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Dict, Deque, List, Optional, Any, AsyncGenerator
from dataclasses import dataclass
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
    OPENROUTER_API_KEY: str
    HERMES_MODEL: str = "openrouter/free"
    MAX_HISTORY: int = 500
    SESSION_FILE: Path = Path("session.json")
    LOG_LEVEL: str = "INFO"
    USER_COOLDOWN_SECONDS: int = 30
    REQUEST_TIMEOUT: int = 30
    STREAM_TIMEOUT: int = 120
    THREAD_INACTIVITY_MINUTES: int = 10
    THREAD_AUTO_ARCHIVE_MINUTES: int = 60

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("Falta DISCORD_TOKEN en .env")
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPEN_ROUTER_API_KEY")
        if not api_key:
            raise ValueError("Falta OPENROUTER_API_KEY (o OPEN_ROUTER_API_KEY) en .env")
        return cls(
            DISCORD_TOKEN=token,
            OPENROUTER_API_KEY=api_key,
            HERMES_MODEL=os.getenv("HERMES_MODEL", "openrouter/free"),
            MAX_HISTORY=int(os.getenv("MAX_HISTORY", "500")),
        )

CFG = Config.from_env()

# ─── Logging estructurado (sin secrets, sin contenido) ───
logging.basicConfig(
    level=getattr(logging, CFG.LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("discord-openrouter-bot")

# ─── Rate limit simple por usuario ───
_user_last_request: Dict[int, float] = {}

def check_rate_limit(user_id: int) -> Optional[float]:
    """Retorna segundos restantes si en cooldown, None si OK."""
    now = time.monotonic()
    last = _user_last_request.get(user_id, 0)
    elapsed = now - last
    if elapsed < CFG.USER_COOLDOWN_SECONDS:
        return CFG.USER_COOLDOWN_SECONDS - elapsed
    _user_last_request[user_id] = now
    return None

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
            tmp.replace(self.session_file)
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

# ─── Thread management ───
async def create_thread_session(message: discord.Message, user_msg: str) -> discord.Thread:
    """Crea hilo a partir de mención en canal normal."""
    thread_name = f"💬 {message.author.display_name} • {user_msg[:50]}"
    thread = await message.create_thread(
        name=thread_name[:100],
        auto_archive_duration=CFG.THREAD_AUTO_ARCHIVE_MINUTES,
        reason="Sesión de chat con bot"
    )
    # Copiar historial del canal al hilo (contexto compartido)
    history = session.get_history(message.channel.id)
    for msg in history:
        session.add(thread.id, msg["role"], msg["content"], msg.get("author", ""))
    
    _active_threads[message.channel.id] = {
        "thread_id": thread.id,
        "last_activity": time.monotonic(),
        "creator_id": message.author.id,
    }
    log.info(f"Hilo creado: {thread.id} para canal {message.channel.id}")
    return thread


async def update_thread_activity(channel_id: int) -> None:
    """Actualiza timestamp de actividad del hilo."""
    if channel_id in _active_threads:
        _active_threads[channel_id]["last_activity"] = time.monotonic()


async def archive_thread_session(channel_id: int) -> None:
    """Archiva hilo y limpia tracking."""
    data = _active_threads.pop(channel_id, None)
    if not data:
        return
    thread = bot.get_channel(data["thread_id"])
    if thread and isinstance(thread, discord.Thread):
        try:
            await thread.edit(archived=True, locked=True)
            log.info(f"Hilo archivado por inactividad: {thread.id}")
        except Exception as e:
            log.warning(f"Error archivando hilo {thread.id}: {e}")
    # Opcional: limpiar sesión del hilo
    # session.clear(data["thread_id"])


async def thread_cleanup_loop() -> None:
    """Background task: revisa hilos inactivos cada minuto."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = time.monotonic()
            inactive = []
            for channel_id, data in _active_threads.items():
                idle_minutes = (now - data["last_activity"]) / 60
                if idle_minutes >= CFG.THREAD_INACTIVITY_MINUTES:
                    inactive.append(channel_id)
            
            for channel_id in inactive:
                await archive_thread_session(channel_id)
                
        except Exception as e:
            log.error(f"Error en thread_cleanup_loop: {e}")
        
        await asyncio.sleep(60)  # cada minuto

# ─── Cliente OpenRouter (OpenAI-compatible) ───
class OpenRouterClient:
    """Cliente asíncrono para OpenRouter API (OpenAI-compatible)."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"
        self._session: Optional[aiohttp.ClientSession] = None

    @asynccontextmanager
    async def _get_session(self):
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=CFG.REQUEST_TIMEOUT, sock_read=CFG.STREAM_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        yield self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def chat_stream(self, messages: List[dict]) -> AsyncGenerator[str, None]:
        """Genera chunks de respuesta desde OpenRouter /chat/completions."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 4000,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/animusicl/membrillo-bot",
            "X-Title": "Membrillo Discord Bot",
        }
        url = f"{self.base_url}/chat/completions"

        async with self._get_session() as sess:
            try:
                async with sess.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 429:
                        raise RuntimeError("RATE_LIMIT")
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"OpenRouter HTTP {resp.status}: {text[:200]}")

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
                raise RuntimeError("No se puede conectar a OpenRouter. ¿Internet/VPN?")
            except asyncio.TimeoutError:
                raise RuntimeError("Timeout conectando a OpenRouter")
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(f"Error OpenRouter: {e}")

openrouter = OpenRouterClient(CFG.OPENROUTER_API_KEY, CFG.HERMES_MODEL)

# ─── Utilidades de formato ───
SYSTEM_PROMPT = (
    "Eres un asistente útil en Discord. "
    "Responde en español, sé conciso, directo y útil. "
    "Mantén el contexto de la conversación compartida."
)

def build_messages(history: List[dict]) -> List[dict]:
    """Construye la lista de mensajes para OpenRouter con system prompt."""
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
    log.info(f"Modelo: {CFG.HERMES_MODEL}")
    try:
        synced = await bot.tree.sync()
        log.info(f"Slash commands sincronizados: {len(synced)}")
    except Exception as e:
        log.error(f"Error sincronizando comandos: {e}")

    # Iniciar thread cleanup loop
    global _thread_cleanup_task
    _thread_cleanup_task = bot.loop.create_task(thread_cleanup_loop())
    log.info("Thread cleanup loop iniciado")

    # Health check server en EL MISMO loop del bot
    bot.loop.create_task(start_health_server())

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # DM → respuesta directa
    if isinstance(message.channel, discord.DMChannel):
        await handle_openrouter_message(message)
        return

    # Hilo activo → responder SIN mención (si bot participa)
    if isinstance(message.channel, discord.Thread):
        # Verificar si este hilo es nuestro
        parent_id = message.channel.parent_id
        if parent_id in _active_threads and _active_threads[parent_id]["thread_id"] == message.channel.id:
            await update_thread_activity(parent_id)
            await handle_openrouter_message(message)
            return
        # Si no es nuestro hilo, solo guardar contexto pasivo
        session.add(message.channel.id, "user", message.content, str(message.author))
        return

    # Canal normal: mención @bot O mensaje que empieza con "membri" → crear hilo + responder
    content_lower = message.content.lower().strip()
    is_mention = bot.user.mentioned_in(message)
    is_membri = content_lower.startswith("membri")
    
    if is_mention or is_membri:
        await handle_openrouter_message(message)
        return

    # Guardar mensaje de usuario en historial (contexto pasivo)
    session.add(message.channel.id, "user", message.content, str(message.author))

    await bot.process_commands(message)

async def handle_openrouter_message(message: discord.Message):
    """Procesa mención @bot o DM/hilo y responde con streaming."""
    channel = message.channel
    user_id = message.author.id

    # Rate limit
    retry_after = check_rate_limit(user_id)
    if retry_after is not None:
        await channel.send(f"⏳ Espera {retry_after:.0f}s antes de otro mensaje (protección de cuota gratuita).")
        return

    # Limpiar mención del bot Y prefijo "membri"
    user_msg = (
        message.content
        .replace(f"<@!{bot.user.id}>", "")
        .replace(f"<@{bot.user.id}>", "")
        .strip()
    )
    
    # Quitar prefijo "membri" (case insensitive)
    if user_msg.lower().startswith("membri"):
        user_msg = user_msg[6:].lstrip(" :,-")

    if not user_msg:
        await channel.send("👋 ¡Hola! Mencióname o escríbeme por DM para charlar.")
        return

    # Validar longitud
    if len(user_msg) > 4000:
        await channel.send("❌ Mensaje muy largo (máx 4000 chars).")
        return

    # Si es canal normal (no hilo ni DM), crear hilo
    target_channel = channel
    parent_channel_id = None
    if isinstance(channel, discord.TextChannel) and not isinstance(channel, discord.Thread):
        parent_channel_id = channel.id
        thread = await create_thread_session(message, user_msg)
        target_channel = thread
        # Enviar confirmación en el hilo
        await thread.send(f"🧵 **Hilo creado**. Continuamos aquí sin necesidad de mencionarme.\n\n{message.author.mention}: {user_msg}")

    # Guardar mensaje usuario (en hilo o canal original)
    session.add(target_channel.id, "user", user_msg, str(message.author))
    # También guardar en canal padre para contexto compartido
    if parent_channel_id:
        session.add(parent_channel_id, "user", user_msg, str(message.author))

    # Preparar historial (usar historial del hilo/canal objetivo)
    history = session.get_history(target_channel.id)
    messages = build_messages(history)

    # Streaming response
    async with target_channel.typing():
        try:
            response_chunks = []
            reply_msg = None
            start_time = time.monotonic()

            async for chunk in openrouter.chat_stream(messages):
                response_chunks.append(chunk)
                full = "".join(response_chunks)

                if reply_msg is None:
                    reply_msg = await target_channel.send(full + "▌")
                elif len(full) % 50 == 0:
                    try:
                        await reply_msg.edit(content=full + "▌")
                    except (discord.NotFound, discord.HTTPException):
                        reply_msg = await target_channel.send(full + "▌")

            final_text = "".join(response_chunks).strip()
            latency = time.monotonic() - start_time

            if reply_msg:
                await reply_msg.edit(content=final_text)
            else:
                await target_channel.send(final_text)

            # Guardar respuesta del bot
            session.add(target_channel.id, "assistant", final_text, bot.user.name)
            if parent_channel_id:
                session.add(parent_channel_id, "assistant", final_text, bot.user.name)
            
            # Actualizar actividad del hilo
            if parent_channel_id:
                await update_thread_activity(parent_channel_id)

            log.info(f"Respuesta OK | canal={target_channel.id} user={user_id} latency={latency:.1f}s chars={len(final_text)}")

        except RuntimeError as e:
            if str(e) == "RATE_LIMIT":
                await target_channel.send("⚠️ Cuota gratuita agotada (429). Intenta en unos minutos.")
                log.warning(f"Rate limit 429 | user={user_id}")
            else:
                await target_channel.send(f"❌ {e}")
                log.error(f"Error OpenRouter: {e} | user={user_id}")
        except Exception as e:
            await target_channel.send("❌ Error inesperado. Intenta de nuevo.")
            log.exception(f"Error en handle_openrouter_message | user={user_id}")

# ─── Slash Commands ───
@bot.tree.command(name="chat", description="Habla con el bot (igual que mencionar)")
@app_commands.describe(mensaje="Qué quieres decirle")
async def slash_chat(interaction: discord.Interaction, mensaje: str):
    await interaction.response.defer(thinking=True)
    class FakeMessage:
        def __init__(self, content, channel, author):
            self.content = content
            self.channel = channel
            self.author = author
    fake = FakeMessage(mensaje, interaction.channel, interaction.user)
    await handle_openrouter_message(fake)

@bot.tree.command(name="reset", description="Borra el historial de este canal")
async def slash_reset(interaction: discord.Interaction):
    session.clear(interaction.channel.id)
    await interaction.response.send_message("🗑️ Historial de este canal borrado.", ephemeral=True)

@bot.tree.command(name="history", description="Mensajes en el historial de este canal")
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
        f"🌐 **API:** OpenRouter\n"
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
        f"• API: OpenRouter\n"
        f"• Max history: `{CFG.MAX_HISTORY}`\n"
        f"• Cooldown usuario: `{CFG.USER_COOLDOWN_SECONDS}s`\n"
        f"• Session file: `{CFG.SESSION_FILE}`",
        ephemeral=True
    )

# ─── Lifecycle ───
async def shutdown():
    await openrouter.close()
    log.info("Recursos liberados")

# ─── Health check server (para Render) ───
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
    log.info("Iniciando Discord + OpenRouter Bot...")
    log.info(f"Modelo: {CFG.HERMES_MODEL}")
    log.info(f"Max history: {CFG.MAX_HISTORY}")
    log.info(f"Cooldown usuario: {CFG.USER_COOLDOWN_SECONDS}s")

    try:
        bot.run(CFG.DISCORD_TOKEN)
    except KeyboardInterrupt:
        log.info("Bot detenido por usuario")
    except Exception as e:
        log.exception(f"Error fatal: {e}")
    finally:
        pass