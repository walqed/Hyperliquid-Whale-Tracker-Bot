# main.py
import os
import sys

# Исправление кодировки для Windows консоли
if os.name == 'nt':  # Windows
    os.system('chcp 65001 > nul')  # UTF-8
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

import logging
import asyncio
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ApplicationBuilder, ContextTypes, filters
from telegram.error import TelegramError

# Импорты из наших модулей
import config
import database
import handlers
import monitoring
from hyperliquid_api import format_fill_message, format_order_message

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('whale_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def notification_sender(app: Application):
    """Асинхронная задача, которая отправляет уведомления из очереди мониторинга."""
    logger.info("Запуск отправителя уведомлений...")
    while True:
        try:
            notification = await monitoring.notification_queue.get()
            chat_id = notification.get("chat_id")
            kind = notification.get("kind", "fill")
            payload = notification.get("payload") or notification.get("fill_data")  # обратная совместимость

            if not chat_id or not payload:
                logger.warning(f"Пропуск пустого уведомления: {notification}")
                monitoring.notification_queue.task_done()
                continue

            # Формируем текст сообщения по типу события
            if kind == "fill":
                message_text = format_fill_message(payload)
            elif kind == "order":
                message_text = format_order_message(payload, notification.get("order_action"))
            else:
                logger.debug(f"Неизвестный тип уведомления '{kind}', пробую формат как fill")
                message_text = format_fill_message(payload)
            
            try:
                await app.bot.send_message(chat_id=chat_id, text=message_text, parse_mode='HTML')
                logger.info(f"Уведомление отправлено в чат {chat_id}")
            except TelegramError as e:
                logger.error(f"Ошибка отправки в чат {chat_id}: {e}")
            
            monitoring.notification_queue.task_done()
        except Exception as e:
            logger.error(f"Критическая ошибка в notification_sender: {e}", exc_info=True)
            await asyncio.sleep(5)  # Пауза перед продолжением

def validate_config():
    """Проверяет корректность конфигурации перед запуском."""
    errors = []
    
    # Проверка токена
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        errors.append("Не указан токен Telegram бота в config.py")
    elif len(config.TELEGRAM_BOT_TOKEN.split(':')) != 2:
        errors.append("Неверный формат токена Telegram бота")
    
    # Проверка порога
    if config.DEFAULT_TRADE_THRESHOLD_USD <= 0:
        errors.append("Порог уведомлений должен быть положительным числом")
    
    # Проверка доступности файла БД
    try:
        db_dir = os.path.dirname(config.DB_FILE) or '.'
        if not os.access(db_dir, os.W_OK):
            errors.append(f"Нет прав на запись в директорию БД: {db_dir}")
    except Exception as e:
        errors.append(f"Ошибка проверки БД: {e}")
    
    return errors

def print_startup_banner():
    """Выводит баннер при запуске."""
    banner = """
═══════════════════════════════════════════════════════════
  🐋 HYPERLIQUID WHALE TRACKER BOT (BETA) 🐋
  
  Отслеживание крупных сделок
  Мгновенные уведомления
  Аналитика позиций
  
  Версия: 2.0 (Simplified)
═══════════════════════════════════════════════════════════
"""
    logger.info(banner)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок."""
    logger.error(f"Исключение при обработке обновления {update}: {context.error}")
    
    # Уведомляем пользователя о технической ошибке
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="<b>Техническая ошибка</b>\n\n"
                     "Произошла временная ошибка. Попробуйте команду снова через несколько секунд.",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

def main():
    """Основная функция для сборки и запуска бота."""
    print_startup_banner()
    
    # Валидация конфигурации
    logger.info("Проверка конфигурации...")
    config_errors = validate_config()
    if config_errors:
        logger.error("КРИТИЧЕСКИЕ ОШИБКИ КОНФИГУРАЦИИ:")
        for error in config_errors:
            logger.error(f"   {error}")
        logger.error("Исправьте ошибки в config.py и перезапустите бота!")
        return False
    
    logger.info("Конфигурация корректна")
    
    # Инициализация БД при старте
    logger.info("Инициализация базы данных...")
    try:
        database.init_db()
        logger.info("База данных готова к работе")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        return False
    
    # Сборка приложения
    logger.info("Создание Telegram бота...")
    try:
        app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()
        logger.info("Telegram бот создан успешно")
    except Exception as e:
        logger.error(f"Ошибка создания бота: {e}")
        return False
    
    # Регистрация обработчиков команд
    logger.info("Регистрация обработчиков команд...")
    command_handlers = {
        "start": handlers.start,
        "add": handlers.add_wallet,
        "remove": handlers.remove_wallet,
        "list": handlers.list_wallets,
        "set_threshold": handlers.set_threshold_command,
        "set_order_threshold": handlers.set_order_threshold_command,
        "positions": handlers.positions_command,
        "leaderboard": handlers.leaderboard_command,
        "top_positions": handlers.top_positions_command,
        # Торговые команды
        # "set_key": handlers.set_key,   # регистрируем ниже с фильтрами
        "trade": handlers.trade,
        "order": handlers.order,
        "orders": handlers.orders,
        "balance": handlers.balance,
        "wallet_activity": handlers.wallet_activity,
        "format": handlers.format_command,
        "help": handlers.help_command,
        # Алиасы к действующим хэндлерам
        "buy": handlers.buy,
        "sell": handlers.sell,
        "close": handlers.close,
        "leverage": handlers.leverage,
        # Отмена лимитных ордеров
        "cancel": handlers.cancel,
    }
    
    for command, handler_func in command_handlers.items():
        app.add_handler(CommandHandler(command, handler_func))
        logger.info(f"   /{command}")

    # Добавляем обработчик callback для навигации по истории сделок
    app.add_handler(CallbackQueryHandler(handlers.wallet_navigation_callback, pattern=r'^wallet_'))
    logger.info("   Callback handler для навигации по кошелькам")
    
    # Добавляем обработчик навигации по частям длинных сообщений
    app.add_handler(CallbackQueryHandler(handlers.handle_navigation, pattern=r'^nav_'))
    logger.info("   Callback handler для навигации по сообщениям")
    
    # Добавляем обработчик навигации по страницам ордеров
    app.add_handler(CallbackQueryHandler(handlers.handle_orders_navigation, pattern=r'^orders_page_'))
    logger.info("   Callback handler для навигации по страницам ордеров")

    # Регистрация set_key: в ЛС обрабатывается, в группах — предупреждение
    app.add_handler(CommandHandler("set_key", handlers.set_key, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("set_key", handlers.set_key_group_warning, filters=filters.ChatType.GROUPS))
    logger.info("   /set_key (PRIVATE only; groups receive warning)")
    
    # Добавление обработчика ошибок
    app.add_error_handler(error_handler)
    logger.info("Обработчик ошибок добавлен")

    # Запуск фонового потока для мониторинга через WebSocket
    logger.info("Запуск WebSocket мониторинга...")
    try:
        monitor_thread = threading.Thread(target=monitoring.monitor_worker, daemon=True)
        monitor_thread.start()
        logger.info("WebSocket мониторинг запущен")
    except Exception as e:
        logger.error(f"Ошибка запуска мониторинга: {e}")
        return False

    # Запуск асинхронной задачи для отправки уведомлений
    async def post_init(app: Application):
        logger.info("Запуск системы уведомлений...")
        # Инициализируем очередь уведомлений с новым event loop
        if monitoring.init_notification_queue():
            asyncio.create_task(notification_sender(app))
        else:
            logger.error("❌ Не удалось инициализировать систему уведомлений")

    app.post_init = post_init
    
    # Запуск бота
    logger.info("Запуск Telegram бота...")
    logger.info("="*50)
    logger.info("БОТ АКТИВЕН И ГОТОВ К РАБОТЕ!")
    logger.info("Пользователи могут писать боту: /start")
    logger.info("Мониторинг сделок активен")
    logger.info("="*50)
    
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем (Ctrl+C)")
        return True
    except Exception as e:
        logger.error(f"Критическая ошибка при работе бота: {e}", exc_info=True)
        return False

if __name__ == '__main__':
    try:
        success = main()
        if success:
            logger.info("Бот завершил работу корректно")
        else:
            logger.error("Бот завершил работу с ошибками")
            exit(1)
    except Exception as e:
        logger.critical(f"Фатальная ошибка: {e}", exc_info=True)
        exit(1)