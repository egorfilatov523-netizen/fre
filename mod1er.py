
import asyncio
import os
import re
import random
import sqlite3
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramAPIError


# =============== НАСТРОЙКИ ===============

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8937288340:AAFOQDffMAesEjonf59nFh89F3n8uTIolgQ").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "5782652474").split(",")
    if x.strip().isdigit()
}

DB_PATH = "bot.db"
WARNING_DELETE_SECONDS = 7

if not BOT_TOKEN:
    raise RuntimeError("Укажи BOT_TOKEN в файле .env")


# =============== БАЗА ДАННЫХ ===============

def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_words (
                word TEXT PRIMARY KEY
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER,
                prize TEXT NOT NULL,
                winners_count INTEGER NOT NULL,
                ends_at REAL NOT NULL,
                active INTEGER DEFAULT 1
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_users (
                giveaway_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                PRIMARY KEY (giveaway_id, user_id)
            )
        """)

        for word in DEFAULT_BAD_WORDS:
            conn.execute(
                "INSERT OR IGNORE INTO banned_words(word) VALUES (?)",
                (word,)
            )


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


# =============== ФИЛЬТР СЛОВ ===============

LOOKALIKES = str.maketrans({
    "ё": "е",
    "a": "а", "c": "с", "e": "е",
    "o": "о", "p": "р", "x": "х",
    "y": "у", "k": "к", "m": "м",
    "t": "т", "h": "н", "b": "в",
    "0": "о", "3": "з",
})


def normalize(text: str) -> str:
    text = text.lower().translate(LOOKALIKES)
    text = re.sub(r"[^а-яa-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_words() -> set[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT word FROM banned_words"
        ).fetchall()

    return {normalize(row["word"]) for row in rows}


banned_words: set[str] = set()
admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}


def find_bad_word(text: str) -> str | None:
    normalized = normalize(text)
    if not normalized:
        return None

    compact = normalized.replace(" ", "")

    for word in banned_words:
        if re.search(
            rf"(?<!\w){re.escape(word)}(?!\w)",
            normalized
        ):
            return word

        # Поиск обхода фильтра через пробелы.
        compact_word = word.replace(" ", "")
        if len(compact_word) >= 5 and compact_word in compact:
            return word

    return None


# =============== ПРАВА АДМИНИСТРАТОРОВ ===============

async def is_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True

    key = (chat_id, user_id)
    now = time.monotonic()

    cached = admin_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]

    try:
        member = await bot.get_chat_member(chat_id, user_id)
        result = member.status in ("administrator", "creator")
        admin_cache[key] = (result, now + 30)
        return result
    except TelegramAPIError:
        return False


async def can_manage(message: Message) -> bool:
    if message.from_user and message.from_user.id in ADMIN_IDS:
        return True

    if message.chat.type in ("group", "supergroup"):
        return await is_group_admin(
            message.bot,
            message.chat.id,
            message.from_user.id if message.from_user else 0
        )

    return False


def private_admin(message: Message) -> bool:
    return bool(
        message.from_user
        and message.from_user.id in ADMIN_IDS
    )


# =============== УВЕДОМЛЕНИЯ ===============

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
    original_text: str,
):
    report = (
        "🚨 Нарушение правил\n\n"
        f"Группа: {chat_title}\n"
        f"Пользователь: {username}\n"
        f"ID: {user_id}\n"
        f"Слово: {bad_word}\n"
        f"Сообщение: {original_text[:1000]}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report)
        except TelegramAPIError:
            pass


# =============== БОТ И РОУТЕР ===============

router = Router()


@router.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "🛡 Бот модерации запущен!\n\n"
        "Команды:\n"
        "/help — помощь\n"
        "/addword слово — добавить слово\n"
        "/delword слово — удалить слово из фильтра\n"
        "/words — список слов\n"
        "/check текст — проверить текст\n"
        "/del — удалить сообщение (ответом)\n"
        "/ban — заблокировать пользователя (ответом)\n"
        "/unban ID — разблокировать пользователя\n"
        "/giveaway минуты победители приз — создать розыгрыш\n"
        "/endgiveaway ID — завершить розыгрыш"
    )


@router.message(Command("help"))
async def help_command(message: Message):
    await start(message)


# =============== ДОБАВИТЬ СЛОВО ===============

@router.message(Command("addword"))
async def add_word(message: Message, command: CommandObject):
    global banned_words

    if not await can_manage(message):
        await message.answer("⛔ Команда доступна только администраторам.")
        return

    word = normalize(command.args or "")

    if not word:
        await message.answer("Использование: /addword запрещённое_слово")
        return

    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO banned_words(word) VALUES (?)",
            (word,)
        )

    banned_words = load_words()
    await message.answer(f"✅ Добавлено в фильтр: {word}")


# =============== УДАЛИТЬ СЛОВО ИЗ ФИЛЬТРА ===============

@router.message(Command("delword"))
async def delete_word(message: Message, command: CommandObject):
    global banned_words

    if not await can_manage(message):
        await message.answer("⛔ Нет доступа.")
        return

    word = normalize(command.args or "")
    if not word:
        await message.answer("Использование: /delword слово")
        return

    with db() as conn:
        cursor = conn.execute(
            "DELETE FROM banned_words WHERE word = ?",
            (word,)
        )

    banned_words = load_words()

    if cursor.rowcount:
        await message.answer(f"✅ Слово удалено: {word}")
    else:
        await message.answer("Такого слова нет в фильтре.")


# =============== СПИСОК СЛОВ ===============

@router.message(Command("words"))
async def show_words(message: Message):
    if not await can_manage(message):
        await message.answer("⛔ Нет доступа.")
        return

    words = sorted(banned_words)
    if not words:
        await message.answer("Список запрещённых слов пуст.")
        return

    result = "🚫 Запрещённые слова:\n\n" + "\n".join(words)

    for i in range(0, len(result), 3500):
        await message.answer(result[i:i + 3500])


# =============== ПРОВЕРКА ТЕКСТА ===============

@router.message(Command("check"))
async def check_text(message: Message, command: CommandObject):
    if not await can_manage(message):
        await message.answer("⛔ Нет доступа.")
        return

    text = command.args or ""
    if not text.strip():
        await message.answer("Использование: /check текст")
        return

    word = find_bad_word(text)

    if word:
        await message.answer(f"🚫 Найдено запрещённое слово: {word}")
    else:
        await message.answer("✅ Запрещённых слов не найдено.")


# =============== УДАЛЕНИЕ СООБЩЕНИЯ ===============

@router.message(Command("del"))
async def delete_message(message: Message):
    if not await can_manage(message):
        await message.answer("⛔ Нет доступа.")
        return

    if not message.reply_to_message:
        await message.answer("Ответь командой /del на сообщение, которое нужно удалить.")
        return

    try:
        await message.reply_to_message.delete()
        await message.delete()
    except TelegramAPIError:
        await message.answer(
            "❌ Не удалось удалить сообщение. Проверь права бота."
        )


# =============== БАН И РАЗБАН ===============

@router.message(Command("ban"))
async def ban_user(message: Message, command: CommandObject):
    if not await can_manage(message):
        await message.answer("⛔ Нет доступа.")
        return

    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Команду /ban нужно использовать в группе.")
        return

    target_id = None

    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())

    if not target_id:
        await message.answer(
            "Ответь на сообщение пользователя командой /ban "
            "или укажи его ID: /ban 123456789"
        )
        return

    if target_id == message.bot.id:
        await message.answer("Нельзя заблокировать самого бота.")
        return

    if target_id in ADMIN_IDS:
        await message.answer("Нельзя заблокировать владельца бота.")
        return

    try:
        await message.bot.ban_chat_member(
            chat_id=message.chat.id,
            user_id=target_id
        )
        await message.answer(f"🔨 Пользователь {target_id} заблокирован.")
    except TelegramAPIError as e:
        await message.answer(
            f"❌ Не удалось заблокировать пользователя.\n{e}"
        )


@router.message(Command("unban"))
async def unban_user(message: Message, command: CommandObject):
    if not await can_manage(message):
        await message.answer("⛔ Нет доступа.")
        return

    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Команду /unban нужно использовать в группе.")
        return

    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: /unban ID_пользователя")
        return

    target_id = int(command.args.strip())

    try:
        await message.bot.unban_chat_member(
            chat_id=message.chat.id,
            user_id=target_id,
            only_if_banned=True
        )
        await message.answer(f"✅ Пользователь {target_id} разблокирован.")
    except TelegramAPIError as e:
        await message.answer(f"❌ Не удалось выполнить разбан.\n{e}")


# =============== РОЗЫГРЫШИ ===============

def giveaway_keyboard(giveaway_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🎉 Участвовать",
                callback_data=f"join:{giveaway_id}"
            )
        ]]
    )


@router.message(Command("giveaway"))
async def create_giveaway(
    message: Message,
    command: CommandObject
):
    if not await can_manage(message):
        await message.answer("⛔ Создавать розыгрыши могут только администраторы.")
        return

    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Создавай розыгрыш в группе.")
        return

    args = (command.args or "").split(maxsplit=2)

    if len(args) < 3 or not args[0].isdigit() or not args[1].isdigit():
        await message.answer(
            "Использование:\n"
            "/giveaway минуты количество_победителей приз\n\n"
            "Пример:\n"
            "/giveaway 60 2 AirPods Pro"
        )
        return

    minutes = int(args[0])
    winners_count = int(args[1])
    prize = args[2].strip()

    if not 1 <= minutes <= 10080:
        await message.answer("Длительность должна быть от 1 до 10080 минут.")
        return

    if not 1 <= winners_count <= 50:
        await message.answer("Количество победителей должно быть от 1 до 50.")
        return

    ends_at = time.time() + minutes * 60

    with db() as conn:
        cursor = conn.execute(
            """INSERT INTO giveaways
               (chat_id, prize, winners_count, ends_at)
               VALUES (?, ?, ?, ?)""",
            (message.chat.id, prize, winners_count, ends_at)
        )
        giveaway_id = cursor.lastrowid

    text = (
        f"🎉 РОЗЫГРЫШ №{giveaway_id}\n\n"
        f"🎁 Приз: {prize}\n"
        f"🏆 Победителей: {winners_count}\n"
        f"⏳ Завершение: через {minutes} мин.\n\n"
        "Нажми кнопку ниже, чтобы участвовать!"
    )

    try:
        sent = await message.answer(
            text,
            reply_markup=giveaway_keyboard(giveaway_id)
        )

        with db() as conn:
            conn.execute(
                "UPDATE giveaways SET message_id = ? WHERE id = ?",
                (sent.message_id, giveaway_id)
            )

        asyncio.create_task(
            finish_giveaway_later(message.bot, giveaway_id, minutes * 60)
        )

    except TelegramAPIError as e:
        with db() as conn:
            conn.execute(
                "UPDATE giveaways SET active = 0 WHERE id = ?",
                (giveaway_id,)
            )
        await message.answer(f"Не удалось опубликовать розыгрыш: {e}")


@router.callback_query(F.data.startswith("join:"))
async def join_giveaway(callback: CallbackQuery):
    if not callback.from_user:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return

    giveaway_id_text = callback.data.split(":", 1)[1]

    if not giveaway_id_text.isdigit():
        await callback.answer("Некорректный розыгрыш.", show_alert=True)
        return

    giveaway_id = int(giveaway_id_text)

    with db() as conn:
        giveaway = conn.execute(
            "SELECT * FROM giveaways WHERE id = ? AND active = 1",
            (giveaway_id,)
        ).fetchone()

        if not giveaway or giveaway["ends_at"] <= time.time():
            await callback.answer("Розыгрыш уже завершён.", show_alert=True)
            return

        conn.execute(
            """INSERT OR IGNORE INTO giveaway_users
               (giveaway_id, user_id, username)
               VALUES (?, ?, ?)""",
            (
                giveaway_id,
                callback.from_user.id,
                callback.from_user.username or callback.from_user.full_name
            )
        )

        count = conn.execute(
            "SELECT COUNT(*) FROM giveaway_users WHERE giveaway_id = ?",
            (giveaway_id,)
        ).fetchone()[0]

    await callback.answer(f"Ты участвуешь! Участников: {count}", show_alert=True)


async def finish_giveaway_later(bot: Bot, giveaway_id: int, delay: int):
    await asyncio.sleep(delay)
    await finish_giveaway(bot, giveaway_id)


async def finish_giveaway(bot: Bot, giveaway_id: int):
    with db() as conn:
        giveaway = conn.execute(
            "SELECT * FROM giveaways WHERE id = ? AND active = 1",
            (giveaway_id,)
        ).fetchone()

        if not giveaway:
            return

        conn.execute(
            "UPDATE giveaways SET active = 0 WHERE id = ?",
            (giveaway_id,)
        )

        users = conn.execute(
            """SELECT user_id, username FROM giveaway_users
               WHERE giveaway_id = ?""",
            (giveaway_id,)
        ).fetchall()

    if not users:
        result_text = (
            f"🎁 Розыгрыш №{giveaway_id} завершён.\n"
            "Никто не принял участие."
        )
    else:
        winners = random.sample(
            list(users),
            min(giveaway["winners_count"], len(users))
        )

        winner_lines = []
        for i, winner in enumerate(winners, start=1):
            name = winner["username"] or str(winner["user_id"])
            winner_lines.append(
                f"{i}. {name} (ID: {winner['user_id']})"
            )

        result_text = (
            f"🏁 РОЗЫГРЫШ №{giveaway_id} ЗАВЕРШЁН!\n\n"
            f"🎁 Приз: {giveaway['prize']}\n"
            f"👥 Участников: {len(users)}\n\n"
            "🏆 Победители:\n" + "\n".join(winner_lines)
        )

    try:
        await bot.send_message(giveaway["chat_id"], result_text)

        if giveaway["message_id"]:
            await bot.edit_message_reply_markup(
                chat_id=giveaway["chat_id"],
                message_id=giveaway["message_id"],
                reply_markup=None
            )
    except TelegramAPIError:
        pass


@router.message(Command("endgiveaway"))
async def end_giveaway_command(
    message: Message,
    command: CommandObject
):
    if not await can_manage(message):
        await message.answer("⛔ Нет доступа.")
        return

    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: /endgiveaway ID_розыгрыша")
        return

    giveaway_id = int(command.args.strip())

    with db() as conn:
        giveaway = conn.execute(
            "SELECT * FROM giveaways WHERE id = ? AND active = 1",
            (giveaway_id,)
        ).fetchone()

    if not giveaway:
        await message.answer("Активный розыгрыш с таким ID не найден.")
        return

    if giveaway["chat_id"] != message.chat.id:
        await message.answer("Этот розыгрыш создан в другой группе.")
        return

    await finish_giveaway(message.bot, giveaway_id)


# =============== АВТОМОДЕРАЦИЯ ===============

@router.message()
async def moderate(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return

    user = message.from_user

    if not user or user.is_bot:
        return

    text = message.text or message.caption or ""

    if not text or text.startswith("/") or not banned_words:
        return

    if await is_group_admin(message.bot, message.chat.id, user.id):
        return

    bad_word = find_bad_word(text)

    if not bad_word:
        return

    try:
        await message.delete()
    except TelegramAPIError:
        # Без права удаления не получится убрать сообщение.
        return

    username = f"@{user.username}" if user.username else user.full_name

    try:
        warning = await message.answer(
            f"⚠️ {username}, ваше сообщение было удалено.\n"
            "Причина: нарушение правил модерации."
        )
        asyncio.create_task(delete_later(warning, WARNING_DELETE_SECONDS))
    except TelegramAPIError:
        pass

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


# =============== ЗАПУСК ===============

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

