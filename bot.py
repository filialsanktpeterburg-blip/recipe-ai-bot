"""Telegram-бот, который составляет рецепты из имеющихся продуктов."""

import asyncio
import logging
import os
import re

from gigachat import GigaChat
from telegram import BotCommand, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


class RedactSecretsFilter(logging.Filter):
    """Скрывает Telegram-токены, если библиотека добавила их в лог."""

    token_pattern = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self.token_pattern.sub("bot<СКРЫТО>", record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


for root_handler in logging.getLogger().handlers:
    root_handler.addFilter(RedactSecretsFilter())

# httpx на уровне INFO печатает полный адрес Telegram API, содержащий токен.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

START_TEXT = (
    "Здравствуйте! Я — ИИ-помощник «Рецепт из холодильника» 🍳\n\n"
    "Напишите продукты, которые у вас есть, через запятую. Например:\n"
    "картофель, яйца, сыр, лук\n\n"
    "Я придумаю одно простое блюдо и дам пошаговый рецепт."
)

HELP_TEXT = (
    "Как пользоваться ботом:\n"
    "1. Посмотрите, какие продукты есть дома.\n"
    "2. Напишите их одним сообщением через запятую.\n"
    "3. Подождите несколько секунд — рецепт создаёт искусственный интеллект.\n\n"
    "Пример: курица, рис, морковь, лук\n\n"
    "Команды:\n"
    "/start — начать работу\n"
    "/help — показать эту подсказку\n"
    "/about — информация о проекте"
)

ABOUT_TEXT = (
    "Это учебный проект. Бот передаёт список продуктов в GigaChat через API "
    "и возвращает ответ, созданный искусственным интеллектом."
)


def get_required_env(name: str) -> str:
    """Возвращает обязательную переменную окружения или завершает запуск."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


def extract_answer(response: object) -> str:
    """Извлекает текст из ответа актуальной или совместимой версии SDK."""
    messages = getattr(response, "messages", None)
    if messages:
        content = getattr(messages[0], "content", None)
        if isinstance(content, str):
            return content.strip()
        if content:
            text = getattr(content[0], "text", None)
            if text:
                return str(text).strip()

    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content:
            return str(content).strip()

    raise RuntimeError("GigaChat вернул ответ в неизвестном формате")


def generate_recipe(products: str) -> str:
    """Отправляет список продуктов в GigaChat и возвращает готовый рецепт."""
    credentials = get_required_env("GIGACHAT_CREDENTIALS")

    prompt = f"""
Ты — доброжелательный кулинарный ИИ-помощник «Рецепт из холодильника».
Пользователь перечислил продукты: {products}

Составь ОДИН простой и реалистичный рецепт на русском языке.
Правила ответа:
1. Используй прежде всего перечисленные продукты.
2. Можно считать, что дома есть только вода, соль, перец и растительное масло.
3. Если без дополнительного продукта совсем нельзя обойтись, укажи его отдельно
   в строке «Дополнительно понадобится».
4. Укажи название блюда, примерное время, число порций, список ингредиентов и
   5–8 понятных пошаговых действий.
5. В конце добавь короткий совет по безопасному приготовлению.
6. Не используй Markdown-разметку, звёздочки и таблицы — только обычный текст.
7. Не отвечай на посторонние вопросы. Если вместо продуктов прислан другой текст,
   попроси перечислить продукты.
""".strip()

    with GigaChat(
        credentials=credentials,
        scope="GIGACHAT_API_PERS",
        model="GigaChat",
        verify_ssl_certs=False,
        timeout=60,
        max_retries=2,
    ) as client:
        response = client.chat.create(prompt)

    return extract_answer(response)


async def send_long_message(update: Update, text: str) -> None:
    """Отправляет длинный ответ частями, не превышая лимит Telegram."""
    if not update.message:
        return

    chunk_size = 4000
    for start in range(0, len(text), chunk_size):
        await update.message.reply_text(text[start : start + chunk_size])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(START_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(HELP_TEXT)


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(ABOUT_TEXT)


async def handle_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.message or not update.message.text:
        return

    products = update.message.text.strip()
    if len(products) < 3:
        await update.message.reply_text(
            "Напишите хотя бы один продукт. Например: яйца, сыр, помидор"
        )
        return

    if len(products) > 1000:
        await update.message.reply_text(
            "Список получился слишком длинным. Сократите его до основных продуктов."
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    status_message = await update.message.reply_text(
        "Изучаю продукты и придумываю рецепт… ⏳"
    )

    try:
        recipe = await asyncio.to_thread(generate_recipe, products)
        await status_message.delete()
        await send_long_message(update, recipe)
    except Exception:
        logger.exception("Не удалось получить рецепт")
        await status_message.edit_text(
            "Не получилось связаться с ИИ. Попробуйте ещё раз через минуту."
        )


async def post_init(application: Application) -> None:
    """Добавляет команды в стандартное меню Telegram."""
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Начать работу"),
            BotCommand("help", "Как пользоваться"),
            BotCommand("about", "Об этом проекте"),
        ]
    )


def main() -> None:
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not telegram_token:
        telegram_token = get_required_env("BOT_TOKEN")

    application = (
        Application.builder().token(telegram_token).post_init(post_init).build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_products)
    )

    logger.info("Бот запущен")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
