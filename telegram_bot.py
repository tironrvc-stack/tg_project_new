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
API_ID = 39023948
API_HASH = "4fe47fcdb69a5c9886d7c4ac3069caa4"
SESSION_STRING = "1ApWapzMBuxvqQ4GR_NP7787nHHcNe0zHd4jobUFZ7ynt3S8IKTWWPfoXrhQef2TVH16XaUg00peb7g3JDShl2Q3hCpFIkewXvXPG2sN7LX6eUubKPudF9zd3O0jWqRPUlOiIgSq4YAPxUTuAE9PSsOJZ1kMquqQEuEWkDb0TQRty6oSgk18qOvcea_LdT8OMatYD_uspfKc4k_2GYSQrDWAu3Vybv8MspcNwTZ11sZ2XIsuXbdhqFsjbPS3v6eqxsqwpXvCDwSnwNpOOSQWu1dU2ayvLQflJ7sPVXStXqhXkJep1z17xGueEn7awkhDcSco9fjHoa893XKMVH4uWF8cJ3Aq9FkE="
TARGET_CHANNEL = "@svodkarkd"

SOURCES = [
    "@Ateobreaking", "@ru2ch", "@bazabazon", "@ostorozhno_novosti",
    "@bbbreaking", "@sotaproject", "@ToBeOr_Official", "@mobilizationnews",
    "@tvrain", "@teamnavalny", "@stories_media", "@istories_media",
    "@proektproekt", "@dossiercenter", "@theinsider", "@novaya_europe",
    "@novaya_pishet", "@truexanewsua", "@news_sirena", "@news_kremlin",
    "@kremlin_secrets",
]

POLL_INTERVAL = 60
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
            if now() - msg.date.timestamp() > 120:
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

    # === ТЕСТ: пересылаем последний пост из первого канала ===
    log.info("=== STARTUP TEST: пробуем переслать последний пост из %s ===", SOURCES[0])
    try:
        target_entity = await client.get_entity(TARGET_CHANNEL)
        log.info("TARGET OK: %s (id=%s)", getattr(target_entity, "title", TARGET_CHANNEL), target_entity.id)
        msgs = await client.get_messages(resolved[0], limit=1)
        if msgs:
            test_msg = msgs[0]
            log.info("TEST MSG: id=%s date=%s text=%s", test_msg.id, test_msg.date, repr((test_msg.text or "")[:60]))
            await client.forward_messages(target_entity, test_msg)
            log.info("=== TEST FORWARD OK ===")
        else:
            log.warning("=== TEST: нет сообщений в канале ===")
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

    await asyncio.gather(
        client.run_until_disconnected(),
        polling_loop(client, resolved),
    )

asyncio.run(main())
