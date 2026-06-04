import asyncio
import logging
import random
import sqlite3
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = ""

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()


# ─── DB ───────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect("productivity.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id       INTEGER PRIMARY KEY,
                goal     TEXT NOT NULL,
                deadline TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text    TEXT NOT NULL,
                date    TEXT NOT NULL,
                done    INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()

def upsert_user(user_id: int, goal: str, deadline: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (id, goal, deadline) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET goal=excluded.goal, deadline=excluded.deadline",
            (user_id, goal, deadline)
        )
        conn.commit()

def get_user(user_id: int):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

def get_today_tasks(user_id: int):
    today = date.today().isoformat()
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE user_id=? AND date=?", (user_id, today)
        ).fetchall()

def save_tasks(user_id: int, texts: list[str]):
    today = date.today().isoformat()
    with get_db() as conn:
        conn.executemany(
            "INSERT INTO tasks (user_id, text, date, done) VALUES (?, ?, ?, 0)",
            [(user_id, t, today) for t in texts]
        )
        conn.commit()

def toggle_task(task_id: int) -> int:
    with get_db() as conn:
        row = conn.execute("SELECT done FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return 0
        new_done = 0 if row["done"] else 1
        conn.execute("UPDATE tasks SET done=? WHERE id=?", (new_done, task_id))
        conn.commit()
        return new_done

def count_all_done(user_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE user_id=? AND done=1", (user_id,)
        ).fetchone()
        return row["cnt"] if row else 0


# ─── TASK GENERATOR ───────────────────────────────────────────────────────────

# Keyword → themed subtask templates
KEYWORD_TEMPLATES = {
    "английск": [
        "Выучить 15 новых слов по теме «{topic}»",
        "Послушать подкаст на английском 20 минут",
        "Написать 5 предложений о своём дне на английском",
        "Пройти один урок на языковой платформе",
        "Посмотреть короткое видео без субтитров",
        "Повторить неправильные глаголы — группа {n}",
    ],
    "python": [
        "Написать небольшой скрипт на тему «{topic}»",
        "Разобрать один раздел документации Python",
        "Решить {n} задачу на LeetCode / CodeWars",
        "Прочитать одну статью по Python и сделать заметки",
        "Написать тесты для вчерашнего кода",
        "Отрефакторить один из своих старых скриптов",
    ],
    "программир": [
        "Написать {n}0 строк кода по проекту",
        "Разобрать одну тему из роадмапа",
        "Сделать commit с понятным описанием",
        "Посмотреть разбор алгоритма и законспектировать",
        "Прочитать главу технической книги",
        "Потратить час на pet-проект без отвлечений",
    ],
    "фитнес": [
        "Сделать {n}0-минутную тренировку",
        "Пройти 8 000 шагов за день",
        "Выполнить утреннюю растяжку 15 минут",
        "Записать сегодняшние упражнения в дневник",
        "Выпить 2 литра воды до вечера",
        "Лечь спать до 23:00",
    ],
    "читать": [
        "Прочитать {n}0 страниц книги",
        "Выписать {n} ключевых мыслей из прочитанного",
        "Найти следующую книгу из списка и начать её",
        "Обсудить прочитанное или записать мини-рецензию",
        "Читать минимум 30 минут без телефона",
        "Повторить заметки с прошлой недели",
    ],
    "стартап": [
        "Поговорить с одним потенциальным пользователем",
        "Обновить описание продукта / лендинг",
        "Написать один пост / заметку о проекте",
        "Разобрать одного конкурента и зафиксировать выводы",
        "Сделать одну фичу или улучшение за сессию",
        "Проверить метрики и записать динамику",
    ],
}

UNIVERSAL_TEMPLATES = [
    "Выделить 1 час глубокой работы по теме «{goal_short}» без отвлечений",
    "Сделать {n} конкретный шаг к цели и записать результат",
    "Повторить материал за последние 3 дня — {n}0 минут",
    "Найти один полезный ресурс по теме и добавить в закладки",
    "Написать 5-минутный итог дня: что получилось, что нет",
    "Убрать одно отвлечение, которое мешает работе над целью",
    "Поделиться прогрессом с кем-то или записать публично",
    "Сформулировать главный вопрос по теме и найти ответ",
]

def detect_templates(goal: str) -> list[str]:
    goal_lower = goal.lower()
    for key, templates in KEYWORD_TEMPLATES.items():
        if key in goal_lower:
            return templates
    return UNIVERSAL_TEMPLATES

def generate_tasks(goal: str, count: int = 5) -> list[str]:
    templates = detect_templates(goal)
    # Extract a short keyword from goal for substitution
    words = [w for w in goal.split() if len(w) > 3]
    goal_short = words[0] if words else goal[:12]
    topic_words = ["основы", "практика", "теория", "применение", "навык"]
    topic = random.choice(topic_words)
    n = random.randint(2, 5)

    pool = templates + UNIVERSAL_TEMPLATES
    selected = random.sample(pool, min(count, len(pool)))

    result = []
    for t in selected:
        try:
            task = t.format(goal_short=goal_short, topic=topic, n=n)
        except KeyError:
            task = t
        result.append(task)
    return result


# ─── KEYBOARDS ────────────────────────────────────────────────────────────────

def tasks_keyboard(tasks) -> InlineKeyboardMarkup:
    buttons = []
    for task in tasks:
        icon = "✅" if task["done"] else "❌"
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {task['text']}",
                callback_data=f"toggle:{task['id']}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── HANDLERS ─────────────────────────────────────────────────────────────────

# /start — collect goal
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! 👋 Я помогу отслеживать прогресс этим летом.\n\n"
        "Напиши свою главную цель на лето — одним предложением:"
    )
    dp["waiting_goal"].add(message.from_user.id)


@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    if user_id not in dp["waiting_goal"]:
        return

    goal = message.text.strip()
    deadline = date(date.today().year, 8, 31).isoformat()
    upsert_user(user_id, goal, deadline)
    dp["waiting_goal"].discard(user_id)

    days_left = (date(date.today().year, 8, 31) - date.today()).days
    await message.answer(
        f"✅ Цель сохранена!\n\n"
        f"🎯 <b>{goal}</b>\n"
        f"📅 Дедлайн: 31 августа {date.today().year}\n"
        f"⏳ До дедлайна: <b>{days_left} дней</b>\n\n"
        f"Используй /tasks чтобы получить задачи на сегодня.",
        parse_mode="HTML"
    )


@dp.message(Command("goal"))
async def cmd_goal(message: Message):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала задай цель через /start")
        return
    days_left = (date.fromisoformat(user["deadline"]) - date.today()).days
    await message.answer(
        f"🎯 <b>Твоя цель:</b>\n{user['goal']}\n\n"
        f"📅 Дедлайн: {user['deadline']}\n"
        f"⏳ Осталось: <b>{days_left} дней</b>",
        parse_mode="HTML"
    )


@dp.message(Command("tasks"))
async def cmd_tasks(message: Message):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала задай цель через /start")
        return

    tasks = get_today_tasks(message.from_user.id)
    if not tasks:
        generated = generate_tasks(user["goal"], count=5)
        save_tasks(message.from_user.id, generated)
        tasks = get_today_tasks(message.from_user.id)

    today_str = date.today().strftime("%d.%m.%Y")
    await message.answer(
        f"📋 <b>Задачи на {today_str}</b>\nНажми, чтобы отметить выполнение:",
        reply_markup=tasks_keyboard(tasks),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("toggle:"))
async def toggle_handler(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    toggle_task(task_id)

    tasks = get_today_tasks(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=tasks_keyboard(tasks))
    await callback.answer()


@dp.message(Command("progress"))
async def cmd_progress(message: Message):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала задай цель через /start")
        return

    days_left = max((date.fromisoformat(user["deadline"]) - date.today()).days, 1)
    total_done = count_all_done(message.from_user.id)
    total_expected = days_left * 5

    percent = min(round(total_done / total_expected * 100, 1), 100)
    filled = int(percent / 10)
    bar = "█" * filled + "░" * (10 - filled)

    if percent < 20:
        mood = "Старт положен. Главное — не останавливаться."
    elif percent < 50:
        mood = "Хорошее начало. Держи темп."
    elif percent < 75:
        mood = "Больше половины пути позади. Так держать."
    elif percent < 95:
        mood = "Финишная прямая. Дожми."
    else:
        mood = "Цель достигнута. Уважение."

    await message.answer(
        f"📊 <b>Прогресс к цели</b>\n\n"
        f"Выполнено задач: {total_done} / {total_expected}\n"
        f"[{bar}] {percent}%\n\n"
        f"{mood}",
        parse_mode="HTML"
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    init_db()
    dp["waiting_goal"] = set()
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
