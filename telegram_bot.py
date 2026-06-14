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
SESSION_STRING = "1ApWapzMBu1NyFDuIod_aWp1By2BQmh6JqfZ4c19ddYy6EUM52UGoVEMQuAejZIdsuxC3w2ipbcFDSoa-xNiiXq6XnU9IUzt0zNaO87YkxK2zfLppbeJvp1t53ciTZyw7fiv9lJxz-SmeCCUJ6HNBNKJhSF_Z-unwYh0g-ZRPlNB_VwV_EOOFQwO9mURaMjnEt_6Qd3BqQ-WNpaEgZG9McnXu7em4if5LHjQ8fbbfJ_XCPIQeedXsaEdwq0Xo9M-QDxuXIFNlY8wbbL00UKZ3__tk8jATHhX9LJY88oGToFbxMi9BJUWbMoHDGYXVa6pbRIpg-0CXai74-d9-5j51lESnBJmknvg="
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
POLL_LIMIT = 10
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

def _seen_group(grouped_id: int) -> bool:
    fp = f"group:{grouped_id}"
    return conn.execute("SELECT 1 FROM seen WHERE fp=?", (fp,)).fetchone() is not None

def _mark_group(grouped_id: int):
    fp = f"group:{grouped_id}"
    conn.execute("INSERT OR REPLACE INTO seen VALUES (?,?)", (fp, now()))
    conn.commit()

async def safe_forward(client, msg, username):
    loop = asyncio.get_event_loop()

    # Если это часть медиагруппы — пересылаем всю группу целиком
    if msg.grouped_id:
        already = await loop.run_in_executor(None, _seen_group, msg.grouped_id)
        if already:
            return False
        # Получаем все сообщения группы
        group_msgs = await client.get_messages(
            await client.get_entity(username),
            min_id=msg.id - 15,
            max_id=msg.id + 15,
            limit=20
        )
        group = [m for m in group_msgs if m.grouped_id == msg.grouped_id]
        group.sort(key=lambda m: m.id)
        if group:
            await client.forward_messages(TARGET_CHANNEL, group)
            await loop.run_in_executor(None, _mark_group, msg.grouped_id)
            log.info("FORWARDED GROUP from @%s (%d msgs, grouped_id=%s)", username, len(group), msg.grouped_id)
        return True

    # Обычное одиночное сообщение
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

try:
    asyncio.run(main())
except Exception as e:
    log.error("FATAL: %s", e)
    raise
