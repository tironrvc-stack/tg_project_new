import time
import sqlite3
import hashlib
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import FloodWaitError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# =====================
API_ID = 33712211
API_HASH = "1e351585faf127c9232bae691b7465bd"
SESSION_STRING = "1ApWapzMBu1Er1J5ILZptA2JL0JW7BPz2ty9-OcjtqqlanX0s0ioSJUI59-886xdH59ZrStKIlqfKxXbKJ-9Ed0tj7RjjsB1OQFUVtGD-xsvILJQEloUfTCYQeP_QaZBD_Ah5pNjTOIr2C8aikC7Y59sLyvUbjgTWThIns_olq5lcCZT2xcxceH94wevxaY8YxWVaTdU10iMFkLsM8Rj83j36LXF592lt9odZfKMm_eZCwsKgiWFk65c2EX5G55bHQWOg9M_rCojPD7Ovd9iuR2V4vYUNwOxtUTA8k_hVqVBs6hPPb3qXbo0evx6w-wZs144smtXlnsf88jk2HYI0-Hze3Dv7Gy4="
TARGET_CHANNEL = "@svodkarkd"

SOURCES = [
    "@Ateobreaking", "@ru2ch", "@bazabazon", "@ostorozhno_novosti",
    "@bbbreaking", "@sotaproject", "@ToBeOr_Official", "@mobilizationnews",
    "@tvrain", "@teamnavalny", "@stories_media", "@istories_media",
    "@proektproekt", "@dossiercenter", "@theinsider", "@novaya_europe",
    "@novaya_pishet", "@truexanewsua", "@news_sirena", "@news_kremlin",
    "@kremlin_secrets",
]

POLL_INTERVAL = 30
POLL_LIMIT = 5
# =====================

def now():
    return int(time.time())

conn = sqlite3.connect("/tmp/dedup.sqlite3", check_same_thread=False)
conn.execute("CREATE TABLE IF NOT EXISTS seen (fp TEXT PRIMARY KEY, ts INT)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON seen(ts)")
conn.commit()
conn.execute("DELETE FROM seen WHERE ts < ?", (now() - 7 * 86400,))
conn.commit()

def _seen(fp: str) -> bool:
    return conn.execute("SELECT 1 FROM seen WHERE fp=?", (fp,)).fetchone() is not None

def _mark(fp: str):
    conn.execute("INSERT OR REPLACE INTO seen VALUES (?,?)", (fp, now()))
    conn.commit()

async def safe_forward(client, msg, username):
    loop = asyncio.get_event_loop()
    fp = hashlib.sha256(f"{username}:{msg.id}".encode()).hexdigest()
    already = await loop.run_in_executor(None, _seen, fp)
    if already:
        return False
    await client.forward_messages(TARGET_CHANNEL, msg)
    await loop.run_in_executor(None, _mark, fp)
    log.info("FORWARDED from @%s (msg_id=%s)", username, msg.id)
    return True

async def poll_channel(client, entity):
    username = getattr(entity, "username", None) or str(entity.id)
    try:
        async for msg in client.iter_messages(entity, limit=POLL_LIMIT):
            if now() - msg.date.timestamp() > 180:
                break
            await safe_forward(client, msg, username)
    except Exception as e:
        log.error("POLL ERROR @%s: %s", username, e)

async def polling_loop(client, resolved):
    log.info("Polling loop started (interval=%ds)", POLL_INTERVAL)
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        for entity in resolved:
            await poll_channel(client, entity)
            await asyncio.sleep(1)

async def main():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Сессия недействительна! Сгенерируйте новый SESSION_STRING.")

    # Резолвим каналы
    resolved = []
    for src in SOURCES:
        try:
            entity = await client.get_entity(src)
            resolved.append(entity)
            log.info("OK  %-30s → id=%s", src, entity.id)
        except Exception as e:
            log.error("FAIL %-30s → %s", src, e)

    if not resolved:
        raise RuntimeError("Не удалось зарезолвить ни один канал.")

    log.info("Зарезолвлено: %d / %d", len(resolved), len(SOURCES))

    # Подписываемся на каналы
    for entity in resolved:
        uname = getattr(entity, "username", entity.id)
        try:
            await client(JoinChannelRequest(entity))
            log.info("JOINED @%s", uname)
        except FloodWaitError as e:
            log.warning("FloodWait %ds, жду...", e.seconds)
            await asyncio.sleep(e.seconds + 2)
            await client(JoinChannelRequest(entity))
        except Exception as e:
            log.info("SKIP join @%s (%s)", uname, e)

    # Тест пересылки при старте
    log.info("=== STARTUP TEST ===")
    try:
        target_entity = await client.get_entity(TARGET_CHANNEL)
        log.info("TARGET OK: %s (id=%s)", getattr(target_entity, "title", TARGET_CHANNEL), target_entity.id)
        msgs = await client.get_messages(resolved[0], limit=1)
        if msgs:
            await client.forward_messages(target_entity, msgs[0])
            log.info("=== TEST FORWARD OK ===")
    except Exception as e:
        log.error("=== TEST FORWARD FAILED: %s ===", e)

    # Обработчик событий
    @client.on(events.NewMessage(chats=resolved))
    async def handler(event):
        try:
            msg = event.message
            chat = await event.get_chat()
            username = getattr(chat, "username", None) or str(chat.id)
            await safe_forward(client, msg, username)
        except Exception as e:
            log.error("EVENT ERROR: %s", e)

    log.info("BOT RUNNING — events + polling active")

    # Запускаем оба цикла, перехватываем любые исключения чтобы не упасть
    async def run_client():
        while True:
            try:
                await client.run_until_disconnected()
            except Exception as e:
                log.error("Client disconnected: %s — reconnecting in 10s", e)
                await asyncio.sleep(10)
                try:
                    await client.connect()
                except Exception as ce:
                    log.error("Reconnect failed: %s", ce)

    async def run_polling():
        while True:
            try:
                await polling_loop(client, resolved)
            except Exception as e:
                log.error("Polling loop crashed: %s — restarting in 10s", e)
                await asyncio.sleep(10)

    await asyncio.gather(
        run_client(),
        run_polling(),
        return_exceptions=True,
    )

asyncio.run(main())
