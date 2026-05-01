"""
Фаундер.Код — Telegram-бот нейропродавец
Квалифицирует лидов и присылает резюме разговора владельцу.

Установка:
  pip install python-telegram-bot anthropic python-dotenv

Запуск:
  cp .env.example .env  # и заполнить значения
  python bot.py
"""

import base64
import binascii
import logging
import asyncio
import os
import sys
from anthropic import Anthropic
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
OWNER_CHAT_ID      = os.getenv("OWNER_CHAT_ID", "")

_missing = [k for k, v in {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "OWNER_CHAT_ID": OWNER_CHAT_ID,
}.items() if not v]
if _missing:
    sys.exit(
        f"Не заданы переменные окружения: {', '.join(_missing)}. "
        "Создайте .env (см. .env.example) или экспортируйте их в среду."
    )

# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Ты — ИИ-ассистент Виктории Музыченко, основательницы Фаундер.Код.
Твоя задача: квалифицировать потенциальных клиентов и записать их на бесплатную диагностику бизнес-процессов.

Сценарий разговора:
1. Задай 3-4 вопроса — по одному, не списком.
   Узнай: чем занимается бизнес, сколько людей, какие процессы самые тяжёлые, пробовали ли автоматизацию.
2. После 3-4 ответов — дай краткий персонализированный вывод: что конкретно стоит разобрать на диагностике.
3. ФИНАЛ:
   - Предложи записаться на бесплатную диагностику.
   - Если клиент готов — попроси имя и удобное время для звонка, затем добавь в конце тег [LEAD_READY]
   - Если отказывается — предложи просто оставить контакт (имя + телефон/tg), затем добавь тег [LEAD_READY]

Правила тона:
- Дружелюбный, живой, профессиональный. Как умный знакомый, который разбирается в бизнесе.
- На «вы», но без канцелярита.
- Показывай что слушаешь: перефразируй детали из ответов клиента.

СТРОГО ЗАПРЕЩЕНО: «Ясно», «Понятно», «Отлично», «Хорошо», «Замечательно», «Прекрасно»,
«Супер», «Отличный вопрос», «Это важно», «Спасибо за ответ», «Интересно», «Конечно»,
«Безусловно», «Разумеется». Реагируй содержательно, сразу переходи к следующей мысли.

Не давай советов и решений — это работа Виктории на диагностике.
Не упоминай, что ты ИИ, если не спрашивают напрямую.
Отвечай коротко: 2-4 предложения на сообщение.
Клиент — собственник или топ-менеджер бизнеса."""

SUMMARY_PROMPT = """На основе этого разговора составь краткое резюме лида для владельца бизнеса.

Формат (строго):
👤 Имя: ...
📞 Контакт: ...
🏢 Бизнес: ...
👥 Команда: ...
⚡ Главная боль: ...
🔧 Автоматизация: (пробовали или нет)
⏰ Удобное время: ...
🎯 Вывод: (одно предложение — стоит ли звонить и почему)

Если какой-то пункт не упоминался в разговоре — напиши «не уточнялось»."""

CHATTING = 1
user_histories: dict[int, list] = {}
user_lead_meta: dict[int, dict] = {}


def decode_start_payload(payload: str) -> dict:
    """Декодирует base64url-payload вида 'name|contact' с лендинга."""
    if not payload:
        return {}
    try:
        s = payload.replace("-", "+").replace("_", "/")
        s += "=" * (-len(s) % 4)
        raw = base64.b64decode(s).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return {}
    parts = raw.split("|", 1)
    name = parts[0].strip() if parts else ""
    contact = parts[1].strip() if len(parts) > 1 else ""
    return {"name": name, "contact": contact}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    user_histories[chat_id] = []
    user_lead_meta[chat_id] = {}

    payload = " ".join(context.args) if context.args else ""
    lead = decode_start_payload(payload)
    if lead:
        user_lead_meta[chat_id] = lead
        user_histories[chat_id].append({
            "role": "user",
            "content": (
                f"[Контекст из формы на сайте] Имя: {lead.get('name') or 'не указано'}, "
                f"контакт: {lead.get('contact') or 'не указан'}. "
                "Поздоровайся по имени, не задавай вопросов про имя/контакт повторно."
            ),
        })

    name = (lead or {}).get("name", "").strip()
    greeting = (
        f"Добрый день, {name}! " if name else "Добрый день! "
    ) + (
        "Я помогаю разобраться, подойдёт ли вам работа с Фаундер.Код.\n\n"
        "Пару вопросов — и станет понятно, стоит ли нам говорить предметно."
    )
    first_q = "Чем занимается ваш бизнес — и сколько человек в команде?"

    await update.message.reply_text(greeting)
    await asyncio.sleep(0.8)
    await update.message.reply_text(first_q)

    user_histories[chat_id].append({"role": "assistant", "content": greeting})
    user_histories[chat_id].append({"role": "assistant", "content": first_q})

    return CHATTING


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    user_text = update.message.text

    if chat_id not in user_histories:
        user_histories[chat_id] = []

    user_histories[chat_id].append({"role": "user", "content": user_text})

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=user_histories[chat_id]
        )
        reply = response.content[0].text
    except Exception as e:
        logger.error(f"Anthropic error: {e}")
        await update.message.reply_text(
            "Что-то пошло не так. Напишите напрямую: @VMuzychenko8"
        )
        return CHATTING

    lead_ready = "[LEAD_READY]" in reply
    clean_reply = reply.replace("[LEAD_READY]", "").strip()

    await update.message.reply_text(clean_reply)
    user_histories[chat_id].append({"role": "assistant", "content": clean_reply})

    if lead_ready:
        await asyncio.sleep(1)
        await send_lead_to_owner(context, chat_id, update.effective_user)
        return ConversationHandler.END

    return CHATTING


async def send_lead_to_owner(context, chat_id: int, user):
    history = user_histories.get(chat_id, [])

    history_text = "\n".join(
        f"{'Клиент' if m['role'] == 'user' else 'Бот'}: {m['content']}"
        for m in history
        if m["role"] in ("user", "assistant")
    )

    try:
        summary_response = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": f"{SUMMARY_PROMPT}\n\nРазговор:\n{history_text}"
            }]
        )
        summary = summary_response.content[0].text
    except Exception as e:
        logger.error(f"Summary error: {e}")
        summary = "Не удалось сформировать резюме — проверьте полный лог."

    tg_link = f"@{user.username}" if user.username else f"tg://user?id={user.id}"
    lead = user_lead_meta.get(chat_id, {})
    form_block = ""
    if lead.get("name") or lead.get("contact"):
        form_block = (
            f"📝 *Из формы на сайте:*\n"
            f"   Имя: {lead.get('name') or '—'}\n"
            f"   Контакт: {lead.get('contact') or '—'}\n\n"
        )

    notification = (
        f"🔔 *Новый лид с сайта*\n\n"
        f"{form_block}"
        f"{summary}\n\n"
        f"💬 Telegram клиента: {tg_link}\n"
        f"🆔 Chat ID: `{chat_id}`"
    )

    try:
        await context.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=notification,
            parse_mode="Markdown"
        )
        logger.info(f"Лид отправлен владельцу. Chat ID клиента: {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки владельцу: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Хорошо, до встречи. Если появятся вопросы — пишите: @VMuzychenko8",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHATTING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chat)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    app.add_handler(conv)

    logger.info("Бот запущен. Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
