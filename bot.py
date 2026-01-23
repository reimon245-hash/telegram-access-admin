import logging
import os
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# === Настройка логирования ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Конфигурация Google Sheets ===
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")  # ID таблицы из URL
CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")  # JSON-ключ в виде строки

# === Состояния пользователя (просто через контекст) ===
USER_STATE_AWAITING_BOOKING_ID = "awaiting_booking_id"

# === Функция подключения к Google Sheets ===
def get_sheet():
    creds_dict = eval(CREDENTIALS_JSON)  # Преобразуем строку в словарь
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).sheet1
    return sheet

# === Обработчик команды /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я виртуальный администратор.\n\nПожалуйста, введите ваш ID бронирования:"
    )
    context.user_data["state"] = USER_STATE_AWAITING_BOOKING_ID

# === Обработчик текстовых сообщений ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state = context.user_data.get("state")
    text = update.message.text.strip()

    if user_state == USER_STATE_AWAITING_BOOKING_ID:
        booking_id = text
        try:
            sheet = get_sheet()
            all_values = sheet.get_all_values()
            headers = all_values[0]
            rows = all_values[1:]

            # Поиск по ID брони
            row_index = None
            for i, row in enumerate(rows):
                if row and len(row) > 0 and row[0] == booking_id:
                    row_index = i + 2  # +1 заголовок +1 (нумерация с 1 в Sheets)
                    break

            if row_index is None:
                await update.message.reply_text(
                    "❌ Бронирование не найдено. Пожалуйста, введите корректный ID бронирования:"
                )
                return

            # Получаем нужные данные
            row = rows[row_index - 2]  # индекс в rows

            def safe_get(col_name, default="—"):
                try:
                    idx = headers.index(col_name)
                    return row[idx] if idx < len(row) else default
                except ValueError:
                    return default

            address = safe_get("Адрес")
            check_in_date = safe_get("Дата заезда")
            check_in_time = safe_get("Время заезда")
            check_out_date = safe_get("Дата выезда")
            check_out_time = safe_get("Время выезда")
            payment_rest = safe_get("Оплата остаток")
            extra_payments = safe_get("Доп Оплаты")
            extra_amount = safe_get("Доп оплаты сумма")
            deposit_amount = safe_get("Залог сумма")

            message = (
                f"✅ Бронирование найдено!\n\n"
                f"📍 Адрес: {address}\n"
                f"📅 Заезд: {check_in_date} в {check_in_time}\n"
                f"📅 Выезд: {check_out_date} в {check_out_time}\n"
                f"💰 Остаток оплаты: {payment_rest}\n"
                f"➕ Доп. оплаты: {extra_payments} ({extra_amount})\n"
                f"🔒 Залог: {deposit_amount}"
            )

            keyboard = [
                [
                    InlineKeyboardButton("Я на месте", callback_data=f"arrived_{booking_id}"),
                    InlineKeyboardButton("Обновить данные брони", callback_data=f"refresh_{booking_id}"),
                ],
                [
                    InlineKeyboardButton("Связаться с менеджером", callback_data=f"manager_{booking_id}"),
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(message, reply_markup=reply_markup)

            # Сохраняем ID в user_data для будущих действий
            context.user_data["booking_id"] = booking_id
            context.user_data["row_index"] = row_index

        except Exception as e:
            logger.error(f"Ошибка при работе с таблицей: {e}")
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

# === Обработчик кнопок ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("arrived_"):
        booking_id = data.replace("arrived_", "")
        try:
            sheet = get_sheet()
            all_values = sheet.get_all_values()
            headers = all_values[0]
            rows = all_values[1:]

            for i, row in enumerate(rows):
                if row and row[0] == booking_id:
                    row_index = i + 2
                    col_idx = headers.index("Я на месте") + 1  # колонки в Sheets нумеруются с 1
                    sheet.update_cell(row_index, col_idx, "✅")
                    await query.edit_message_text("✅ Отметка «Я на месте» установлена!")
                    return

            await query.edit_message_text("❌ Бронирование не найдено.")
        except Exception as e:
            logger.error(f"Ошибка при обновлении 'Я на месте': {e}")
            await query.edit_message_text("⚠️ Не удалось обновить статус.")

    elif data.startswith("refresh_"):
        booking_id = data.replace("refresh_", "")
        context.user_data["state"] = USER_STATE_AWAITING_BOOKING_ID
        await query.message.reply_text("🔄 Введите ID бронирования снова:")
        await query.message.delete()

    elif data.startswith("manager_"):
        booking_id = data.replace("manager_", "")
        try:
            sheet = get_sheet()
            all_values = sheet.get_all_values()
            headers = all_values[0]
            rows = all_values[1:]

            for row in rows:
                if row and row[0] == booking_id:
                    manager = row[headers.index("Менеджер")] if "Менеджер" in headers else "не указан"
                    await query.message.reply_text(f"📞 Свяжитесь с менеджером: {manager}")
                    return
            await query.message.reply_text("❌ Менеджер не найден.")
        except Exception as e:
            logger.error(f"Ошибка при получении менеджера: {e}")
            await query.message.reply_text("⚠️ Не удалось получить контакт менеджера.")

# === Основная функция запуска ===
def main():
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_TOKEN:
        raise ValueError("Не задан TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_handler))

    application.run_polling()

if __name__ == "__main__":
    main()