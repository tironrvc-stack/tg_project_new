import time
import sqlite3
import hashlib
import asyncio

from telethon import TelegramClient, events
from telethon.sessions import StringSession

# =====================
API_ID = 39023948
API_HASH = "4fe47fcdb69a5c9886d7c4ac3069caa4"

SESSION_STRING = "1ApWapzMBuxvqQ4GR_NP7787nHHcNe0zHd4jobUFZ7ynt3S8IKTWWPfoXrhQef2TVH16XaUg00peb7g3JDShl2Q3hCpFIkewXvXPG2sN7LX6eUubKPudF9zd3O0jWqRPUlOiIgSq4YAPxUTuAE9PSsOJZ1kMquqQEuEWkDb0TQRty6oSgk18qOvcea_LdT8OMatYD_uspfKc4k_2GYSQrDWAu3Vybv8MspcNwTZ11sZ2XIsuXbdhqFsjbPS3v6eqxsqwpXvCDwSnwNpOOSQWu1dU2ayvLQflJ7sPVXStXqhXkJep1z17xGueEn7awkhDcSco9fjHoa893XKMVH4uWF8cJ3Aq9FkE="

TARGET_CHANNEL = "@svodkarkd"

SOURCES = [
    "Ateobreaking",
    "ru2ch",
    "bazabazon",
    "ostorozhno_novosti",
    "bbbreaking",
    "sotaproject",
    "ToBeOr_Official",
    "mobilizationnews",
    "tvrain",
    "teamnavalny",
    "stories_media",
    "istories_media",
    "proektproekt",
    "dossiercenter",
    "theinsider",
    "novaya_europe",
    "novaya_pishet",
    "truexanewsua",
    "news_sirena",
    "news_kremlin",
    "kremlin_secrets",
]

# =====================

def now():
    return int(time.time())

conn = sqlite3.connect("dedup.sqlite3")
conn.execute("CREATE TABLE IF NOT EXISTS seen (fp TEXT PRIMARY KEY, ts INT)")

def seen(fp):
    return conn.execute("SELECT 1 FROM seen WHERE fp=?", (fp,)).fetchone()

def mark(fp):
    conn.execute("INSERT OR REPLACE INTO seen VALUES (?,?)", (fp, now()))
    conn.commit()


async def main():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

    @client.on(events.NewMessage(chats=SOURCES))
    async def handler(event):
        try:
            msg = event.message
            chat = await event.get_chat()
            username = getattr(chat, "username", "unknown")

            fp = hashlib.sha256(f"{username}:{msg.id}".encode()).hexdigest()

            if seen(fp):
                return

            await client.forward_messages(TARGET_CHANNEL, msg)
            mark(fp)

            print("FORWARDED:", username)

        except Exception as e:
            print("ERROR:", e)

    await client.start()
    print("BOT RUNNING (RAILWAY READY)")
    await client.run_until_disconnected()


asyncio.run(main())
