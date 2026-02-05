import asyncio
import logging
import json
import os
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

# ────────────────────────────────────────────────
BOT_TOKEN = '8588881813:AAGBFM87eIDq-RcFlfqoR8yDkHhOm1JSKTw'
CHANNEL_ID = -1003325257490                 # ← реальный ID канала
ADMIN_IDS = [867371536]                     # ← твой ID из логов
# ────────────────────────────────────────────────

SCHEDULED_POSTS_FILE = "scheduled_posts.json"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Загрузка сохранённых постов
scheduled_posts = []
if os.path.exists(SCHEDULED_POSTS_FILE):
    try:
        with open(SCHEDULED_POSTS_FILE, "r", encoding="utf-8") as f:
            scheduled_posts = json.load(f)
    except Exception as e:
        logging.error(f"Ошибка загрузки scheduled_posts.json: {e}")
        scheduled_posts = []


def save_scheduled_posts():
    try:
        with open(SCHEDULED_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(scheduled_posts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения scheduled_posts.json: {e}")


class PostForm(StatesGroup):
    text = State()
    media = State()
    buttons = State()
    date = State()
    time = State()
    confirm = State()


class EditForm(StatesGroup):
    edit_text = State()
    edit_time = State()
    edit_buttons = State()
    edit_confirm = State()


def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Создать пост", callback_data="create_post")],
        [InlineKeyboardButton(text="📅 Мои отложенные посты", callback_data="list_scheduled")],
    ])


@dp.message(Command(commands=['start']))
async def cmd_start(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Доступ запрещён.")
        return
    await message.answer("Привет! Выберите действие:", reply_markup=get_main_menu())


@dp.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню", reply_markup=get_main_menu())
    await callback.answer()


# ─── Создание поста ───
@dp.callback_query(lambda c: c.data == "create_post")
async def start_create(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "✍️ Напишите текст поста\n"
        "(поддерживается <b>жирный</b>, <i>курсив</i>, ссылки, эмодзи)"
    )
    await state.set_state(PostForm.text)


@dp.message(PostForm.text)
async def process_text(message: Message, state: FSMContext):
    if not message.text.strip():
        await message.answer("Текст не может быть пустым.")
        return

    await state.update_data(text=message.html_text.strip())

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Добавить фото/видео", callback_data="add_media")],
        [InlineKeyboardButton(text="➡️ Без медиа", callback_data="no_media")],
    ])

    await message.answer("Хотите прикрепить медиа?", reply_markup=kb)
    await state.set_state(PostForm.media)


@dp.callback_query(PostForm.media, lambda c: c.data in ("add_media", "no_media"))
async def process_media_choice(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete_reply_markup()

    if callback.data == "no_media":
        await state.update_data(media_type=None, media_id=None)
        await ask_for_buttons(callback.message, state)
        return

    await callback.message.answer("Пришлите фото или видео (одно).")


@dp.message(PostForm.media, lambda m: m.photo or m.video)
async def process_media(message: Message, state: FSMContext):
    if message.photo:
        media_id = message.photo[-1].file_id
        media_type = 'photo'
    elif message.video:
        media_id = message.video.file_id
        media_type = 'video'
    else:
        await message.answer("Пришлите фото или видео.")
        return

    await state.update_data(media_type=media_type, media_id=media_id)
    await ask_for_buttons(message, state)


async def ask_for_buttons(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Добавить кнопки", callback_data="add_buttons")],
        [InlineKeyboardButton(text="Без кнопок", callback_data="no_buttons")],
    ])
    await message.answer("Добавить кнопки?", reply_markup=kb)
    await state.set_state(PostForm.buttons)


@dp.callback_query(PostForm.buttons, lambda c: c.data in ("add_buttons", "no_buttons"))
async def process_buttons_choice(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete_reply_markup()

    if callback.data == "no_buttons":
        await state.update_data(buttons=None)
        await ask_date(callback.message, state)
        return

    await callback.message.answer(
        "Пришлите кнопки по одной на строку:\n\n"
        "Текст | https://ссылка\n"
        "Текст 2 | https://ссылка2\n\n"
        "Максимум 8 кнопок."
    )


@dp.message(PostForm.buttons)
async def process_buttons(message: Message, state: FSMContext):
    lines = [l.strip() for l in message.text.splitlines() if l.strip()]

    if not lines:
        await message.answer("Не распознано ни одной кнопки.")
        return

    buttons_list = []
    for line in lines:
        if '|' not in line:
            await message.answer(f"Неверный формат:\n{line}")
            return
        txt, url = [p.strip() for p in line.split('|', 1)]
        if not txt or not url.startswith(('http://', 'https://')):
            await message.answer(f"Ошибка в строке:\n{line}")
            return
        buttons_list.append(InlineKeyboardButton(text=txt, url=url))

    if len(buttons_list) > 8:
        await message.answer("Максимум 8 кнопок.")
        return

    rows = [buttons_list[i:i+2] for i in range(0, len(buttons_list), 2)]
    markup = InlineKeyboardMarkup(inline_keyboard=rows)

    await state.update_data(buttons=markup)
    await ask_date(message, state)


async def ask_date(message: Message, state: FSMContext):
    await message.answer("📅 Дата: <code>ДД.ММ.ГГГГ</code>")
    await state.set_state(PostForm.date)


@dp.message(PostForm.date)
async def process_date(message: Message, state: FSMContext):
    try:
        dt = datetime.strptime(message.text.strip(), '%d.%m.%Y').date()
        await state.update_data(pub_date=dt)
    except ValueError:
        await message.answer("Формат: ДД.ММ.ГГГГ")
        return

    await message.answer("⏰ Время: <code>ЧЧ:ММ</code>")
    await state.set_state(PostForm.time)


@dp.message(PostForm.time)
async def process_time(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        tm = datetime.strptime(message.text.strip(), '%H:%M').time()
        when = datetime.combine(data['pub_date'], tm)
    except ValueError:
        await message.answer("Формат: ЧЧ:ММ")
        return

    if when <= datetime.now():
        await message.answer("Укажите будущее время.")
        return

    await state.update_data(pub_datetime=when)

    text = data.get('text', '')
    media_type = data.get('media_type')
    buttons = data.get('buttons')
    dt_str = when.strftime("%d.%m.%Y в %H:%M")

    preview = f"<b>Текст:</b>\n{text or '—'}\n\n"
    if media_type:
        preview += f"<b>Медиа:</b> {media_type}\n"
    if buttons:
        preview += "<b>Кнопки:</b> да\n"
    preview += f"<b>Время:</b> {dt_str}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Запланировать", callback_data="confirm_publish")],
        [InlineKeyboardButton(text="✖ Отменить", callback_data="cancel_publish")],
    ])

    await message.answer(f"Проверьте пост:\n\n{preview}", reply_markup=kb)
    await state.set_state(PostForm.confirm)


@dp.callback_query(lambda c: c.data in ("confirm_publish", "cancel_publish"))
async def process_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()

    if callback.data == "cancel_publish":
        await callback.message.answer("Создание отменено.", reply_markup=get_main_menu())
        await state.clear()
        return

    data = await state.get_data()
    text = data.get('text', '')
    media_type = data.get('media_type')
    media_id = data.get('media_id')
    buttons = data.get('buttons')
    when = data['pub_datetime']

    job_id = f"post_{callback.from_user.id}_{int(when.timestamp())}"

    async def send_post():
        try:
            if media_type == 'photo':
                await bot.send_photo(CHANNEL_ID, photo=media_id, caption=text, reply_markup=buttons)
            elif media_type == 'video':
                await bot.send_video(CHANNEL_ID, video=media_id, caption=text, reply_markup=buttons)
            else:
                await bot.send_message(CHANNEL_ID, text=text, reply_markup=buttons)

            global scheduled_posts
            scheduled_posts = [p for p in scheduled_posts if p["job_id"] != job_id]
            save_scheduled_posts()

        except Exception as e:
            logging.error(f"Ошибка публикации: {e}")

    scheduler.add_job(send_post, DateTrigger(run_date=when), id=job_id)

    post_info = {
        "job_id": job_id,
        "user_id": callback.from_user.id,
        "time_iso": when.isoformat(),
        "time_str": when.strftime("%d.%m.%Y в %H:%M"),
        "text_preview": escape(text[:80] + ("..." if len(text or "") > 80 else text or "[без текста]")),
        "has_media": bool(media_type),
        "media_type": media_type,
        "has_buttons": bool(buttons)
    }

    scheduled_posts.append(post_info)
    save_scheduled_posts()

    await callback.message.answer(
        f"✅ Запланировано на <b>{post_info['time_str']}</b>",
        reply_markup=get_main_menu()
    )
    await state.clear()


# ─── Список отложенных постов ───
@dp.callback_query(lambda c: c.data == "list_scheduled")
async def show_scheduled(callback: CallbackQuery):
    if not scheduled_posts:
        await callback.message.edit_text(
            "Нет отложенных постов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
            ])
        )
        await callback.answer()
        return

    text = "📅 <b>Отложенные посты</b>\n\n"
    kb_rows = []

    for i, post in enumerate(scheduled_posts, 1):
        safe_preview = escape(post['text_preview'])
        line = f"{i}. {post['time_str']} — {safe_preview}"
        if post.get("has_media"):
            line += f" + {post.get('media_type', '')}"
        if post.get("has_buttons"):
            line += " + кнопки"
        text += line + "\n\n"

        kb_rows.append([
            InlineKeyboardButton(text=f"👁 Посмотреть №{i}", callback_data=f"preview_{post['job_id']}"),
            InlineKeyboardButton(text=f"✏ Редактировать №{i}", callback_data=f"edit_{post['job_id']}"),
            InlineKeyboardButton(text=f"❌ Удалить №{i}", callback_data=f"delete_{post['job_id']}"),
        ])

    kb_rows.append([InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")])

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


# ─── Предпросмотр ───
@dp.callback_query(lambda c: c.data.startswith("preview_"))
async def preview_post(callback: CallbackQuery):
    job_id = callback.data.split("_", 1)[1]
    post = next((p for p in scheduled_posts if p["job_id"] == job_id), None)

    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    preview = f"<b>Предпросмотр поста</b>\n\n"
    preview += f"<b>Время:</b> {post['time_str']}\n\n"
    preview += f"<b>Текст:</b>\n{escape(post.get('text_preview', '[без текста]'))}\n\n"

    if post.get("has_media"):
        preview += f"<b>Медиа:</b> {post.get('media_type', '—')}\n"
    if post.get("has_buttons"):
        preview += "<b>Кнопки:</b> есть\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← К списку", callback_data="list_scheduled")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="back_to_menu")]
    ])

    await callback.message.edit_text(preview, reply_markup=kb)
    await callback.answer()


# ─── Удаление поста ───
@dp.callback_query(lambda c: c.data.startswith("delete_"))
async def delete_post(callback: CallbackQuery):
    job_id = callback.data.split("_", 1)[1]

    post_index = next((i for i, p in enumerate(scheduled_posts) if p["job_id"] == job_id), -1)

    if post_index == -1:
        await callback.answer("Пост уже удалён", show_alert=True)
        return

    post = scheduled_posts[post_index]

    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    del scheduled_posts[post_index]
    save_scheduled_posts()

    await callback.answer(f"Пост удалён: {post['time_str']}", show_alert=True)
    await show_scheduled(callback)


# ─── Редактирование поста ───
@dp.callback_query(lambda c: c.data.startswith("edit_"))
async def start_edit(callback: CallbackQuery, state: FSMContext):
    job_id = callback.data.split("_", 1)[1]

    logging.info(f"Попытка редактирования job_id: {job_id}")
    logging.info(f"Доступные job_id: {[p['job_id'] for p in scheduled_posts]}")

    post = next((p for p in scheduled_posts if p["job_id"] == job_id), None)

    if not post:
        await callback.answer("Пост не найден — возможно, он уже опубликован или удалён.", show_alert=True)
        await show_scheduled(callback)
        return

    await state.update_data(
        editing_job_id=job_id,
        old_text=post.get("text_preview", ""),
        old_time_iso=post["time_iso"],
        old_has_buttons=post.get("has_buttons", False)
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить текст",   callback_data=f"edit_text__{job_id}")],
        [InlineKeyboardButton(text="Изменить время",   callback_data=f"edit_time__{job_id}")],
        [InlineKeyboardButton(text="Изменить кнопки",  callback_data=f"edit_buttons__{job_id}")],
        [InlineKeyboardButton(text="← Отмена",         callback_data="cancel_edit")],
    ])

    await callback.message.edit_text(
        f"Редактирование поста от {post['time_str']}\n\nЧто меняем?",
        reply_markup=kb
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "cancel_edit")
async def cancel_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_scheduled(callback)


# ─── Редактирование текста ───
@dp.callback_query(lambda c: c.data.startswith("edit_text__"))
async def edit_text_start(callback: CallbackQuery, state: FSMContext):
    job_id = callback.data.split("__", 1)[1]

    data = await state.get_data()
    await callback.message.edit_text(
        f"Текущий текст:\n{escape(data.get('old_text', '[без текста]'))}\n\n"
        "Введите новый текст:"
    )
    await state.set_state(EditForm.edit_text)


@dp.message(EditForm.edit_text)
async def process_edit_text(message: Message, state: FSMContext):
    if not message.text.strip():
        await message.answer("Текст не может быть пустым.")
        return

    await state.update_data(new_text=message.html_text.strip())
    await ask_edit_confirm(message, state)


# ─── Редактирование времени ───
@dp.callback_query(lambda c: c.data.startswith("edit_time__"))
async def edit_time_start(callback: CallbackQuery, state: FSMContext):
    job_id = callback.data.split("__", 1)[1]

    await callback.message.edit_text(
        "Введите новую дату и время:\n"
        "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\nПример: 15.03.2026 14:30"
    )
    await state.set_state(EditForm.edit_time)


@dp.message(EditForm.edit_time)
async def process_edit_time(message: Message, state: FSMContext):
    try:
        dt_str, tm_str = message.text.strip().split()
        dt = datetime.strptime(dt_str, '%d.%m.%Y').date()
        tm = datetime.strptime(tm_str, '%H:%M').time()
        new_when = datetime.combine(dt, tm)
    except:
        await message.answer("Формат: ДД.ММ.ГГГГ ЧЧ:ММ")
        return

    if new_when <= datetime.now():
        await message.answer("Время должно быть в будущем.")
        return

    await state.update_data(new_datetime=new_when)
    await ask_edit_confirm(message, state)


# ─── Редактирование кнопок ───
@dp.callback_query(lambda c: c.data.startswith("edit_buttons__"))
async def edit_buttons_start(callback: CallbackQuery, state: FSMContext):
    job_id = callback.data.split("__", 1)[1]

    await callback.message.edit_text(
        "Пришлите новые кнопки (или «без кнопок»):\n\n"
        "Текст | https://ссылка\nпо одной на строку"
    )
    await state.set_state(EditForm.edit_buttons)


@dp.message(EditForm.edit_buttons)
async def process_edit_buttons(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    if text in ("без кнопок", "убрать", "нет", "без"):
        await state.update_data(new_buttons=None)
    else:
        lines = [l.strip() for l in message.text.splitlines() if l.strip()]
        buttons_list = []
        for line in lines:
            if '|' not in line: continue
            txt, url = [p.strip() for p in line.split('|', 1)]
            if url.startswith(('http://', 'https://')):
                buttons_list.append(InlineKeyboardButton(text=txt, url=url))

        if buttons_list:
            rows = [buttons_list[i:i+2] for i in range(0, len(buttons_list), 2)]
            markup = InlineKeyboardMarkup(inline_keyboard=rows)
            await state.update_data(new_buttons=markup)
        else:
            await state.update_data(new_buttons=None)

    await ask_edit_confirm(message, state)


async def ask_edit_confirm(message: Message, state: FSMContext):
    data = await state.get_data()
    preview = "Изменения:\n\n"

    if "new_text" in data:
        preview += f"<b>Новый текст:</b>\n{escape(data['new_text'])}\n\n"
    if "new_datetime" in data:
        preview += f"<b>Новое время:</b> {data['new_datetime'].strftime('%d.%m.%Y в %H:%M')}\n\n"
    if "new_buttons" in data:
        preview += f"<b>Кнопки:</b> {'да' if data['new_buttons'] else 'убраны'}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="save_edit")],
        [InlineKeyboardButton(text="✖ Отменить", callback_data="cancel_edit")]
    ])

    await message.answer(preview or "Ничего не изменено.", reply_markup=kb)
    await state.set_state(EditForm.edit_confirm)


@dp.callback_query(EditForm.edit_confirm, lambda c: c.data == "save_edit")
async def save_edit(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    old_job_id = data["editing_job_id"]

    if scheduler.get_job(old_job_id):
        scheduler.remove_job(old_job_id)

    global scheduled_posts
    scheduled_posts = [p for p in scheduled_posts if p["job_id"] != old_job_id]

    new_text = data.get("new_text")
    new_when = data.get("new_datetime")
    new_buttons = data.get("new_buttons")

    old_post = next((p for p in scheduled_posts if p["job_id"] == old_job_id), {})
    final_text = new_text or old_post.get("text_preview", "")
    final_when = new_when or datetime.fromisoformat(old_post["time_iso"])
    final_buttons = new_buttons if "new_buttons" in data else None

    new_job_id = f"post_{callback.from_user.id}_{int(final_when.timestamp())}"

    async def send_post():
        try:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=final_text,
                reply_markup=final_buttons
            )
            scheduled_posts = [p for p in scheduled_posts if p["job_id"] != new_job_id]
            save_scheduled_posts()
        except Exception as e:
            logging.error(f"Ошибка: {e}")

    scheduler.add_job(send_post, DateTrigger(run_date=final_when), id=new_job_id)

    post_info = {
        "job_id": new_job_id,
        "user_id": callback.from_user.id,
        "time_iso": final_when.isoformat(),
        "time_str": final_when.strftime("%d.%m.%Y в %H:%M"),
        "text_preview": escape(final_text[:80] + ("..." if len(final_text) > 80 else "")),
        "has_media": False,
        "media_type": None,
        "has_buttons": bool(final_buttons)
    }

    scheduled_posts.append(post_info)
    save_scheduled_posts()

    await callback.message.edit_text(
        f"✅ Пост обновлён на <b>{post_info['time_str']}</b>",
        reply_markup=get_main_menu()
    )
    await state.clear()


async def main():
    await bot.delete_webhook(drop_pending_updates=True)

    # Очистка мёртвых постов при запуске
    global scheduled_posts
    active_job_ids = {job.id for job in scheduler.get_jobs()}
    scheduled_posts = [p for p in scheduled_posts if p["job_id"] in active_job_ids]
    save_scheduled_posts()
    logging.info(f"После очистки осталось {len(scheduled_posts)} активных постов")

    scheduler.start()
    logging.info("Планировщик запущен")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())