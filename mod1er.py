import asyncio
import os
import re
import sqlite3
import time
import random
from datetime import datetime, timezone

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8937288340:AAFOQDffMAesEjonf59nFh89F3n8uTIolgQ").strip()
ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "5782652474").split(",")
    if value.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "bot.db")

if not BOT_TOKEN:
    raise RuntimeError("Не указан BOT_TOKEN. Добавь BOT_TOKEN=... в файл .env")

DEFAULT_BAD_WORDS = [
    "бля", "блять", "блядь", "бляди", "сука", "суки", "сучка",
    "хуй", "хуя", "хуе", "хер", "пизда", "пиздец", "пизду", "пизды",
    "ебать", "ебал", "ебан", "ебись", "заебал", "заебись", "охуел",
    "мудак", "мудила", "долбоеб", "гандон", "шлюха",
]

LOOKALIKES = str.maketrans({
    "ё": "е", "a": "а", "c": "с", "e": "е", "o": "о",
    "p": "р", "x": "х", "y": "у", "k": "к", "m": "м",
    "t": "т", "h": "н", "b": "в", "0": "о", "3": "з",
})

def normalize(text: str) -> str:
    text = text.lower().translate(LOOKALIKES)
    text = re.sub(r"[^а-яa-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS banned_words (word TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT OR IGNORE INTO banned_words(word) VALUES (?)",
            [(w,) for w in DEFAULT_BAD_WORDS],
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER,
                creator_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                max_participants INTEGER NOT NULL,
                winner_count INTEGER NOT NULL,
                ends_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                winners_json TEXT NOT NULL DEFAULT '[]'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                giveaway_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                joined_at REAL NOT NULL,
                PRIMARY KEY (giveaway_id, user_id)
            )
        """)

def load_words() -> set[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT word FROM banned_words").fetchall()
    return {normalize(row[0]) for row in rows if normalize(row[0])}

banned_words: set[str] = set()
admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}
router = Router()

def find_bad_word(text: str) -> str | None:
    normalized = normalize(text)
    if not normalized:
        return None
    compact_text = normalized.replace(" ", "")
    for word in banned_words:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", normalized):
            return word
        compact_word = word.replace(" ", "")
        if len(compact_word) >= 5 and compact_word in compact_text:
            return word
