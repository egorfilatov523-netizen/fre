import asyncio
import os
import re
import sqlite3
import time

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError


# ================= НАСТРОЙКИ =================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8937288340:AAFOQDffMAesEjonf59nFh89F3n8uTIolgQ").strip()

ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "5782652474").split(",")
    if value.strip().isdigit()
}

DB_PATH = "bot.db"

if not BOT_TOKEN:
    raise RuntimeError(
        "Не указан BOT_TOKEN. Добавь его в файл .env"
    )


# Начальный список. Дополняй его командами /addword.
DEFAULT_BAD_WORDS = [
    "бля", "блять", "блядь", "бляди",
    "сука", "суки", "сучка",
    "хуй", "хуя", "хуе", "хер",
    "пизда", "пиздец", "пизду", "пизды",
    "ебать", "ебал", "ебан", "ебись",
    "заебал", "заебись", "охуел",
    "мудак", "мудила", "долбоеб",
    "гандон", "шлюха",
]


# ================= БАЗА ДАННЫХ =================

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_words (
                word TEXT PRIMARY KEY
            )
        """)

        for word in DEFAULT_BAD_WORDS:
            conn.execute(
                "INSERT OR IGNORE INTO banned_words (word) VALUES (?)",
                (word,)
            )


def load_words() -> set[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT word FROM banned_words"
        ).fetchall()

    return {
        normalize(row[0])
        for row in rows
        if normalize(row[0])
    }


# ================= ПОИСК СЛОВ =================

LOOKALIKES = str.maketrans({
    "ё": "е",
    "a": "а",
    "c": "с",
    "e": "е",
    "o": "о",
    "p": "р",
    "x": "х",
    "y": "у",
    "k": "к",
    "m": "м",
    "t": "т",
    "h": "н",
    "b": "в",
    "0": "о",
    "3": "з",
})


def normalize(text: str) -> str:
    text = text.lower().translate(LOOKALIKES)
    text = re.sub(r"[^а-яa-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


banned_words: set[str] = set()
admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}


def find_bad_word(text: str) -> str | None:
    normalized_text = normalize(text)

    if not normalized_text:
        return None

    compact_text = normalized_text.replace(" ", "")

    for word in banned_words:
        # Обычная проверка отдельных слов и фраз.
        pattern = rf"(?<!\w){re.escape(word)}(?!\w)"

        if re.search(pattern, normalized_text):
            return word

        # Дополнительная проверка обхода фильтра пробелами.
        compact_word = word.replace(" ", "")

        if len(compact_word) >= 5 and compact_word in compact_text:
            return word

    return None


# ================= ПРОВЕРКА АДМИНОВ =================

async def is_chat_admin_fast(
    bot: Bot,
    chat_id: int,
    user_id: int
) -> bool:
    key = (chat_id, user_id)
    now = time.monotonic()

    cached = admin_cache.get(key)

    if cached and cached[1] > now:
        return cached[0]

    try:
        member = await bot.get_chat_member(chat_id, user_id)

        result = member.status in ("administrator", "creator")

        admin_cache[key] = (result, now + 60)

        return result

    except TelegramAPIError:
        # При ошибке API не блокируем обработку сообщения
        # только из-за невозможности проверить администратора.
        return False


# ================= УВЕДОМЛЕНИЯ =================

async def delete_later(message: Message, delay: int = 7):
    await asyncio.sleep(delay)

    try:
        await message.delete()
    except TelegramAPIError:
        pass


async def notify_admins(
    bot: Bot,
    chat_title: str,
    username: str,
    user_id: int,
    bad_word: str,
    original_text: str
):
    if not ADMIN_IDS:
        return

    report = (
        "🚨 Нарушение правил\n\n"
        f"Группа: {chat_title}\n"
        f"Пользователь: {username}\n"
        f"ID пользователя: {user_id}\n"
        f"Запрещённое слово: {bad_word}\n"
        f"Сообщение: {original_text[:1500]}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report)
        except TelegramAPIError:
            # Например, администратор ещё не запускал бота.
            pass


# ================= КОМАНДЫ =================

router = Router()


def is_bot_admin(message: Message) -> bool:
    return (
        message.from_user is not None
        and message.from_user.id in ADMIN_IDS
    )


@router.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "🛡 Бот модерации запущен.\n\n"
        "/help — список команд\n"
        "/addword слово — добавить запрещённое слово\n"
        "/delword слово — удалить слово из фильтра\n"
        "/words — показать список слов\n"
        "/check текст — проверить текст"
    )


@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "🛡 Команды модерации:\n\n"
        "/addword слово — добавить слово\n"
        "/delword слово — удалить слово\n"
        "/words — список запрещённых слов\n"
        "/check текст — проверить текст\n\n"
        "Команды управления доступны только ID из ADMIN_IDS."
    )


@router.message(Command("addword"))
async def add_word(
    message: Message,
    command: CommandObject
):
    global banned_words

    if not is_bot_admin(message):
        await message.answer("⛔ Нет доступа.")
        return

    word = (command.args or "").strip()

    if not word:
        await message.answer(
            "Использование: /addword запрещённое_слово"
        )
        return

    word = normalize(word)

    if not word:
        await message.answer("Не удалось распознать слово.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO banned_words (word) VALUES (?)",
            (word,)
        )

    banned_words = load_words()

    await message.answer(f"✅ Добавлено в фильтр: {word}")


@router.message(Command("delword"))
async def delete_word(
    message: Message,
    command: CommandObject
):
    global banned_words

    if not is_bot_admin(message):
        await message.answer("⛔ Нет доступа.")
        return

    word = normalize((command.args or "").strip())

    if not word:
        await message.answer(
            "Использование: /delword слово"
        )
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM banned_words WHERE word = ?",
            (word,)
        )

    banned_words = load_words()

    if cursor.rowcount:
        await message.answer(f"✅ Удалено из фильтра: {word}")
    else:
        await message.answer("Такого слова нет в списке.")


@router.message(Command("words"))
async def show_words(message: Message):
    if not is_bot_admin(message):
        await message.answer("⛔ Нет доступа.")
        return

    words = sorted(banned_words)

    if not words:
        await message.answer("Список запрещённых слов пуст.")
        return

    # Не отправляем слишком длинное сообщение.
    result = "🚫 Запрещённые слова:\n\n" + "\n".join(words)

    for start in range(0, len(result), 3500):
        await message.answer(result[start:start + 3500])


@router.message(Command("check"))
async def check_text(
    message: Message,
    command: CommandObject
):
    if not is_bot_admin(message):
        await message.answer("⛔ Нет доступа.")
        return

    text = (command.args or "").strip()

    if not text:
        await message.answer("Использование: /check текст")
        return

    bad_word = find_bad_word(text)

    if bad_word:
        await message.answer(
            f"🚫 Найдено запрещённое слово: {bad_word}"
        )
    else:
        await message.answer("✅ Запрещённых слов не найдено.")


# ================= МОДЕРАЦИЯ =================

@router.message()
async def moderate(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return

    user = message.from_user

    if user is None or user.is_bot:
        return

    text = message.text or message.caption or ""

    # Не проверяем сообщения-команды бота.
    if text.startswith("/"):
        return

    if not text or not banned_words:
        return

    # Не модерируем администраторов группы.
    if await is_chat_admin_fast(
        message.bot,
        message.chat.id,
        user.id
    ):
        return

    bad_word = find_bad_word(text)

    if not bad_word:
        return

    # Сначала пытаемся удалить нарушающее сообщение.
    try:
        await message.delete()
    except TelegramAPIError:
        # Без права удаления сообщение останется в группе.
        return

    username = (
        f"@{user.username}"
        if user.username
        else user.full_name
    )

    # Предупреждение сразу после удаления.
    try:
        warning = await message.answer(
            f"⚠️ {username}, сообщение удалено.\n"
            f"Причина: запрещённое слово."
        )

        asyncio.create_task(delete_later(warning, 7))

    except TelegramAPIError:
        pass

    # Отчёт админам не задерживает обработку следующих сообщений.
    asyncio.create_task(
        notify_admins(
            bot=message.bot,
            chat_title=message.chat.title or str(message.chat.id),
            username=username,
            user_id=user.id,
            bad_word=bad_word,
            original_text=text
        )
    )


# ================= ЗАПУСК =================

async def main():
    global banned_words

    init_db()
    banned_words = load_words()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    print("Бот модерации запущен.")
    print(f"Загружено запрещённых слов: {len(banned_words)}")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
