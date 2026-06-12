import time
import sqlite3
import hashlib
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# =====================
API_ID = 39023948
API_HASH = "4fe47fcdb69a5c9886d7c4ac3069caa4"
SESSION_STRING = "1ApWapzMBuxvqQ4GR_NP7787nHHcNe0zHd4jobUFZ7ynt3S8IKTWWPfoXrhQef2TVH16XaUg00peb7g3JDShl2Q3hCpFIkewXvXPG2sN7LX6eUubKPudF9zd3O0jWqRPUlOiIgSq4YAPxUTuAE9PSsOJZ1kMquqQEuEWkDb0TQRty6oSgk18qOvcea_LdT8OMatYD_uspfKc4k_2GYSQrDWAu3Vybv8MspcNwTZ11sZ2XIsuXbdhqFsjbPS3v6eqxsqwpXvCDwSnwNpOOSQWu1dU2ayvLQflJ7sPVXStXqhXkJep1z17xGueEn7awkhDcSco9fjHoa893XKMVH4uWF8cJ3Aq9FkE="
TARGET_CHANNEL = "@svodkarkd"

# FIX 4: добавлены @ для надёжного резолва каналов
SOURCES = [
    "@Ateobreaking",
    "@ru2ch",
    "@bazabazon",
    "@ostorozhno_novosti",
    "@bbbreaking",
    "@sotaproject",
    "@ToBeOr_Official",
    "@mobilizationnews",
    "@tvrain",
    "@teamnavalny",
    "@stories_media",
    "@istories_media",
    "@proektproekt",
    "@dossiercenter",
    "@theinsider",
    "@novaya_europe",
    "@novaya_pishet",
    "@truexanewsua",
    "@news_sirena",
    "@news_kremlin",
    "@kremlin_secrets",
]
# =====================

def now():
    return int(time.time())

# FIX 2: храним БД в /tmp — переживает рестарты процесса, не блокирует деплой
# FIX 3: используем check_same_thread=False, операции обёрнуты в executor
conn = sqlite3.connect("/tmp/dedup.sqlite3", check_same_thread=False)
conn.execute("CREATE TABLE IF NOT EXISTS seen (fp TEXT PRIMARY KEY, ts INT)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON seen(ts)")
conn.commit()

# Очищаем записи старше 7 дней при старте, чтобы БД не росла бесконечно
conn.execute("DELETE FROM seen WHERE ts < ?", (now() - 7 * 86400,))
conn.commit()

def _seen(fp: str) -> bool:
    return conn.execute("SELECT 1 FROM seen WHERE fp=?", (fp,)).fetchone() is not None

def _mark(fp: str):
    conn.execute("INSERT OR REPLACE INTO seen VALUES (?,?)", (fp, now()))
    conn.commit()

async def main():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError(
            "Сессия недействительна! Сгенерируйте новый SESSION_STRING "
            "и обновите переменную окружения на Railway."
        )

    # === ДИАГНОСТИКА: резолвим каждый канал и логируем результат ===
    resolved = []
    for src in SOURCES:
        try:
            entity = await client.get_entity(src)
            title = getattr(entity, "title", src)
            resolved.append(entity)
            log.info("  OK  %-30s → id=%s title=%s", src, entity.id, title)
        except Exception as e:
            log.error("  FAIL %-30s → %s", src, e)

    if not resolved:
        raise RuntimeError("Не удалось зарезолвить ни один канал — проверь, что аккаунт подписан на них.")

    log.info("Зарезолвлено каналов: %d / %d", len(resolved), len(SOURCES))

    # Подписываемся на уже зарезолвленные entity, а не на строки —
    # это гарантирует, что Telethon точно знает id каждого чата
    @client.on(events.NewMessage(chats=resolved))
    async def handler(event):
        try:
            msg = event.message
            chat = await event.get_chat()
            username = getattr(chat, "username", None) or str(chat.id)
            fp = hashlib.sha256(f"{username}:{msg.id}".encode()).hexdigest()

            loop = asyncio.get_event_loop()
            already_seen = await loop.run_in_executor(None, _seen, fp)
            if already_seen:
                return

            await client.forward_messages(TARGET_CHANNEL, msg)
            await loop.run_in_executor(None, _mark, fp)
            log.info("FORWARDED from @%s (msg_id=%s)", username, msg.id)

        except Exception as e:
            log.error("ERROR in handler: %s", e)

    log.info("BOT RUNNING (RAILWAY READY), listening to %d channels", len(resolved))

    # FIX 5: цикл переподключения при обрывах связи
    while True:
        try:
            await client.run_until_disconnected()
        except Exception as e:
            log.error("Disconnected: %s — reconnecting in 10s...", e)
            await asyncio.sleep(10)
            try:
                await client.connect()
            except Exception as ce:
                log.error("Reconnect failed: %s", ce)

asyncio.run(main())
