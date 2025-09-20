# handlers.py
import asyncio
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
# Удалён неиспользуемый импорт из hyperliquid; вместо него используем локальную функцию
# from cryptography.fernet import Fernet
# from hyperliquid.utils.signing import private_key_to_address

import database
import hyperliquid_api
import trading
from config import DEFAULT_TRADE_THRESHOLD_USD, DB_FILE
from logging_utils import logger


def clean_address(args: tuple) -> str | None:
    """Очищает и валидирует адрес кошелька."""
    if not args:
        return None
    address = args[0].strip().strip('<>').lower()
    return address if (address.startswith("0x") and len(address) == 42) else None


# Вспомогательная функция: получить EVM-адрес из приватного ключа
# Использует пакет eth-keys (поставляется через requirements.txt)
def _derive_address_from_private_key(private_key: str) -> str:
    try:
        from eth_keys import keys
        hex_key = private_key[2:] if private_key.startswith("0x") else private_key
        if len(hex_key) != 64:
            raise ValueError("Invalid hex length")
        pk_bytes = bytes.fromhex(hex_key)
        addr = keys.PrivateKey(pk_bytes).public_key.to_checksum_address()
        return addr.lower()
    except Exception as e:
        raise ValueError("Неверный приватный ключ") from e


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение с инструкциями."""
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name or "Трейдер"

    # Не сбрасываем порог на значение по умолчанию при каждом /start
    # Берём текущий порог пользователя из БД (если нет записи, вернётся DEFAULT_TRADE_THRESHOLD_USD)
    threshold = database.get_user_threshold(chat_id)
    order_threshold = database.get_user_order_threshold(chat_id)

    welcome_message = f"""<b>🐋 Hyperliquid Whale Bot (Beta)</b>
<i>by walqed</i> ✨

Привет, {user_name}! 👋

<b>🔧 Управление кошельками:</b>
/add <b>адрес</b> — добавить кошелек
/remove <b>адрес</b> — удалить кошелек  
/list — мои кошельки
/wallet_activity <b>адрес</b> — активность кошелька
/set_threshold <b>сумма</b> — настроить уведомления
/set_order_threshold <b>сумма</b> — порог для ордеров

<b>📊 Аналитика:</b>
/positions <b>адрес</b> — позиции кошелька
/leaderboard <b>[daily/weekly/monthly]</b> <b>[desktop|mobile]</b> — топ трейдеры
/top_positions <b>[period] [N]</b> — позиции топ-N трейдеров

<b>📈 Торговля :</b>
/trade — управление ордерами
/orders — активные ордера
/balance — баланс кошелька

<b>ℹ️ Справка:</b>
/help — подробная информация о терминах

💰 Текущий порог сделок: <code>${threshold:,.0f}</code>
🧱 Порог для ордеров: <code>${order_threshold:,.0f}</code>
    """

    await update.message.reply_html(welcome_message)


async def add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет кошелек для отслеживания."""
    message = update.message.text.strip()
    parts = message.split()

    if len(parts) != 2:
        await update.message.reply_html(
            "<b>❌ Неверный адрес</b>\n\n"
            "🔎 Укажите корректный адрес кошелька"
        )
        return

    address = parts[1]

    if not (address.startswith("0x") and len(address) == 42):
        await update.message.reply_html(
            "<b>❌ Неверный адрес</b>\n\n"
            "🔎 Укажите корректный адрес кошелька"
        )
        return

    database.add_wallet_to_db(update.effective_chat.id, address)

    threshold = database.get_user_threshold(update.effective_chat.id)

    # Сообщение-результат
    await update.message.reply_html(
        f"<b>✅ Кошелек добавлен</b>\n\n"
        f"🔔 Порог уведомлений: <code>${threshold:,.0f}</code>"
    )




async def remove_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет кошелек из отслеживания."""
    address = clean_address(context.args)
    if not address:
        await update.message.reply_html(
            "<b>⚠️ Укажите адрес</b>\n\n"
            "🔍 Используйте: /remove <code>адрес</code>"
        )
        return

    database.remove_wallet_from_db(update.effective_chat.id, address)
    await update.message.reply_html(
        f"<b>🗑️ Кошелек удален</b>\n\n"
        f"📝 <code>{address}</code>"
    )


async def list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список отслеживаемых кошельков."""
    wallets = database.get_wallets_for_user(update.effective_chat.id)
    threshold = database.get_user_threshold(update.effective_chat.id)

    if not wallets:
        await update.message.reply_html(
            "<b>📭 Список пуст</b>\n\n"
            "➕ Добавьте кошелек: <code>/add [адрес_кошелька]</code>\n"
            "💡 <i>Начните отслеживать крупные сделки китов!</i>"
        )
        return

    wallets_text = "\n".join([f"🔍 <code>{w}</code>" for w in wallets])
    message = (
        f"<b>📋 Отслеживаемые кошельки ({len(wallets)})</b>\n\n"
        f"{wallets_text}\n\n"
        f"🔔 Порог: <code>${threshold:,.0f}</code>"
    )
    await update.message.reply_html(message)


async def set_threshold_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Устанавливает порог для уведомлений."""
    chat_id = update.effective_chat.id
    current = database.get_user_threshold(chat_id)

    if not context.args:
        await update.message.reply_html(
            f"<b>⚙️ Текущий порог: <code>${current:,.0f}</code></b>\n\n"
            f"✏️ Изменить: <code>/set_threshold 50000</code>\n"
            f"💡 <i>Чем выше порог, тем реже уведомления</i>"
        )
        return

    try:
        threshold = float(context.args[0])
        if threshold <= 0:
            raise ValueError()

        database.set_user_threshold(chat_id, threshold)
        await update.message.reply_html(
            f"<b>✅ Порог обновлен</b>\n\n"
            f"🆕 Новый: <code>${threshold:,.0f}</code>\n"
            f"🔔 Теперь вы будете получать уведомления о сделках от <code>${threshold:,.0f}</code>"
        )
    except (ValueError, IndexError):
        await update.message.reply_html(
            "<b>❌ Неверный формат</b>\n\n"
            "🔍 Пример: <code>/set_threshold 100000</code>\n"
            "💰 Укажите сумму в долларах"
        )


async def set_order_threshold_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Устанавливает порог для уведомлений об ордерах (размещение/отмена)."""
    chat_id = update.effective_chat.id
    current = database.get_user_order_threshold(chat_id)

    if not context.args:
        await update.message.reply_html(
            f"<b>⚙️ Текущий порог ордеров: <code>${current:,.0f}</code></b>\n\n"
            f"✏️ Изменить: <code>/set_order_threshold 50000</code>\n"
            f"💡 <i>Этот порог применяется только к событиям размещения/отмены ордеров. Для сделок используйте /set_threshold</i>"
        )
        return

    try:
        threshold = float(context.args[0])
        if threshold <= 0:
            raise ValueError()

        database.set_user_order_threshold(chat_id, threshold)
        await update.message.reply_html(
            f"<b>✅ Порог ордеров обновлен</b>\n\n"
            f"🆕 Новый: <code>${threshold:,.0f}</code>\n"
            f"🔔 Теперь вы будете получать уведомления об ордерах от <code>${threshold:,.0f}</code>"
        )
    except (ValueError, IndexError):
        await update.message.reply_html(
            "<b>❌ Неверный формат</b>\n\n"
            "🔍 Пример: <code>/set_order_threshold 100000</code>\n"
            "💰 Укажите сумму в долларах"
        )


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает открытые позиции кошелька."""
    address = clean_address(context.args)
    if not address:
        await update.message.reply_html(
            "<b>⚠️ Укажите адрес кошелька после команды</b>\n\n"
            "📝 Введите команду и через пробел адрес\n"
            "💡 Или используйте <code>/top_positions</code> чтобы посмотреть позиции топ-трейдеров"
        )
        return

    loading_msg = await update.message.reply_html(
        "<b>⏳ Загрузка позиций...</b>"
    )

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, hyperliquid_api.get_user_positions_with_sdk, address)
    open_orders = await loop.run_in_executor(None, hyperliquid_api.get_open_orders, address)
    message = hyperliquid_api.format_user_positions(address, data)

    # На первой странице ордера не показываем - только кнопка для перехода на страницу 2
    # orders_text убираем с первой страницы

    # Роль адреса выводим, если пусты позиции ИЛИ пусты ордера
    positions_empty = not (data and (data.get('assetPositions') or []))
    orders_empty = (not open_orders or len(open_orders) == 0)
    if positions_empty or orders_empty:
        role_data = await loop.run_in_executor(None, hyperliquid_api.get_user_role, address)
        role_info = ""
        if role_data:
            role = role_data.get('role', 'unknown')
            if role == 'user':
                role_info = "👤 обычный"
            elif role == 'subAccount':
                master = role_data.get('master', 'N/A')
                role_info = f"🔗 субаккаунт мастера {master[:10]}..."
            elif role == 'vault':
                role_info = "🏦 vault"
            elif role == 'agent':
                role_info = "🤖 агент"
            else:
                role_info = role
        else:
            role_info = "❓ неизвестно"
        message += f"\nℹ️ Роль адреса: {role_info}"

    # Доп. диагностика спота и подсказка — если пусто и там, и там
    if positions_empty and orders_empty:
        spot_balances = (data.get('balances', []) if isinstance(data, dict) else []) or []
        significant_balances = [b for b in spot_balances if float(b.get('total', 0) or 0) > 100]
        spot_info = ""
        if significant_balances:
            spot_coins = [b.get('coin', 'N/A') for b in significant_balances[:3]]
            spot_info = f"\n💰 Спот: {', '.join(spot_coins)}"
            if len(significant_balances) > 3:
                spot_info += f" +{len(significant_balances)-3}"

        message += (
            f"{spot_info}\n"
            f"💡 Подсказка: лимитные заявки отображаются в разделе открытых ордеров. "
            f"Если маркет-ордер был исполнен и позиция сразу закрылась, активных позиций может не быть."
        )

    # Добавляем кнопку "Вперед" для перехода к ордерам на странице 2
    keyboard = []
    if open_orders and len(open_orders) > 0:  # Если есть ордера
        keyboard.append([InlineKeyboardButton("Открытые ордера ▶️", callback_data=f"orders_page_2_{address}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    # Проверяем длину сообщения и разбиваем на части если необходимо
    max_length = 4000  # Оставляем запас для кнопок
    if len(message) <= max_length:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=loading_msg.message_id,
            text=message,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    else:
        # Разбиваем сообщение на части
        parts = []
        current_part = ""
        lines = message.split('\n')
        
        for line in lines:
            if len(current_part + line + '\n') <= max_length:
                current_part += line + '\n'
            else:
                if current_part:
                    parts.append(current_part.rstrip())
                current_part = line + '\n'
        
        if current_part:
            parts.append(current_part.rstrip())
        
        # Сохраняем части в context для навигации
        context.user_data['message_parts'] = parts
        context.user_data['current_part'] = 0
        
        # Создаем кнопки навигации
        keyboard = []
        if len(parts) > 1:
            keyboard.append([
                InlineKeyboardButton("◀️ Назад", callback_data="nav_prev"),
                InlineKeyboardButton(f"1/{len(parts)}", callback_data="nav_info"),
                InlineKeyboardButton("Вперед ▶️", callback_data="nav_next")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Отправляем первую часть с кнопками
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=loading_msg.message_id,
            text=parts[0],
            parse_mode='HTML',
            reply_markup=reply_markup
        )


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает лидерборд трейдеров."""
    # Разбор аргументов: допускаем в любом порядке timeframe и стиль
    args = [a.lower() for a in (context.args or [])]
    timeframe = 'daily'
    style_override: str | None = None
    for a in args:
        if a in ['daily', 'weekly', 'monthly']:
            timeframe = a
        elif a in ['desktop', 'mobile']:
            style_override = a

    timeframe_emoji = {
        'daily': '📅',
        'weekly': '📆',
        'monthly': '🗓️'
    }

    loading_msg = await update.message.reply_html(
        f"<b>⏳ Загрузка лидерборда...</b>\n{timeframe_emoji.get(timeframe, '📅')} Период: {timeframe}"
    )

    # Определяем предпочтение формата: по базе (desktop по умолчанию) и перезаписываем, если указан в команде
    chat_id = update.effective_chat.id
    pref = database.get_user_format_preference(chat_id)
    chosen = style_override if style_override in ('desktop', 'mobile') else pref
    style = 'mobile' if chosen == 'mobile' else 'desktop'

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, hyperliquid_api.get_leaderboard_data_sync)
    message = hyperliquid_api.format_leaderboard_message(data, timeframe, style=style)

    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=loading_msg.message_id,
        text=message,
        parse_mode='HTML'
    )


# Новая команда: позиции топ-N адресов из лидерборда
async def positions_command_with_address(update: Update, context: ContextTypes.DEFAULT_TYPE, address: str):
    """Показывает открытые позиции кошелька для конкретного адреса."""
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, hyperliquid_api.get_user_positions_with_sdk, address)
    open_orders = await loop.run_in_executor(None, hyperliquid_api.get_open_orders, address)
    message = hyperliquid_api.format_user_positions(address, data)

    # Роль адреса выводим, если пусты позиции ИЛИ пусты ордера
    positions_empty = not (data and (data.get('assetPositions') or []))
    orders_empty = (not open_orders or len(open_orders) == 0)
    if positions_empty or orders_empty:
        role_data = await loop.run_in_executor(None, hyperliquid_api.get_user_role, address)
        role_info = ""
        if role_data:
            role = role_data.get('role', 'unknown')
            if role == 'user':
                role_info = "👤 обычный"
            elif role == 'subAccount':
                master = role_data.get('master', 'N/A')
                role_info = f"🔗 субаккаунт мастера {master[:10]}..."
            elif role == 'vault':
                role_info = "🏦 vault"
            elif role == 'agent':
                role_info = "🤖 агент"
            else:
                role_info = role
        else:
            role_info = "❓ неизвестно"
        message += f"\nℹ️ Роль адреса: {role_info}"

    # Доп. диагностика спота и подсказка — если пусто и там, и там
    if positions_empty and orders_empty:
        spot_balances = (data.get('balances', []) if isinstance(data, dict) else []) or []
        significant_balances = [b for b in spot_balances if float(b.get('total', 0) or 0) > 100]
        spot_info = ""
        if significant_balances:
            spot_coins = [b.get('coin', 'N/A') for b in significant_balances[:3]]
            spot_info = f"\n💰 Спот: {', '.join(spot_coins)}"
            if len(significant_balances) > 3:
                spot_info += f" +{len(significant_balances)-3}"

        message += (
            f"{spot_info}\n"
            f"💡 Подсказка: лимитные заявки отображаются в разделе открытых ордеров. "
            f"Если маркет-ордер был исполнен и позиция сразу закрылась, активных позиций может не быть."
        )

    # Добавляем кнопку "Вперед" для перехода к ордерам на странице 2
    keyboard = []
    if open_orders and len(open_orders) > 0:  # Если есть ордера
        keyboard.append([InlineKeyboardButton("Открытые ордера ▶️", callback_data=f"orders_page_2_{address}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    # Редактируем сообщение
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=update.callback_query.message.message_id,
        text=message,
        parse_mode='HTML',
        reply_markup=reply_markup
    )


async def top_positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Автоматически получает адреса из лидерборда и показывает их позиции.
    Использование: /top_positions [daily|weekly|monthly] [N]
    По умолчанию: daily, N=3
    """
    # Парсим аргументы: допускаем порядок [timeframe] [N] или просто [N]
    timeframe = 'daily'
    top_n = 3
    args = [a.lower() for a in (context.args or [])]

    if args:
        # Если первый аргумент число — трактуем как N
        if args[0].isdigit():
            top_n = max(1, min(10, int(args[0])))
        else:
            if args[0] in ['daily', 'weekly', 'monthly']:
                timeframe = args[0]
            # второй аргумент может быть числом
            if len(args) > 1 and args[1].isdigit():
                top_n = max(1, min(10, int(args[1])))

    loading_msg = await update.message.reply_html(
        f"<b>⏳ Загрузка адресов из лидерборда...</b>\n📅 Период: {timeframe}\n🔟 Количество: {top_n}"
    )

    # Преференс форматирования (desktop по умолчанию)
    chat_id = update.effective_chat.id
    pref = database.get_user_format_preference(chat_id)
    style = 'mobile' if pref == 'mobile' else 'desktop'

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, hyperliquid_api.get_leaderboard_data_sync)
    addresses = hyperliquid_api.extract_top_addresses(data, timeframe=timeframe, top=top_n)

    if not addresses:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=loading_msg.message_id,
            text=("<b>❌ Не удалось получить адреса</b>\n\n"
                 "Возможно, API лидерборда временно недоступно."),
            parse_mode='HTML'
        )
        return

    # Получаем позиции для каждого адреса параллельно через пул исполнителей
    tasks = [loop.run_in_executor(None, hyperliquid_api.get_user_positions_with_sdk, addr) for addr in addresses]
    results = await asyncio.gather(*tasks)

    # Формируем компактный вывод по каждому адресу
    parts: list[str] = []
    header = f"""<b>🧠 Позиции топ-{len(addresses)} трейдеров</b>
📅 Период: {timeframe}
"""
    parts.append(header)

    for addr, user_data in zip(addresses, results):
        # Обработка ошибок/пустых ответов
        if user_data is None:
            if style == 'mobile':
                parts.append(f"\n<pre><code>{addr}</code></pre>\n❗ Ошибка получения данных")
            else:
                parts.append(f"\n<code>{addr}</code>\n❗ Ошибка получения данных")
            continue

        positions = user_data.get('assetPositions', []) or []
        if not positions:
            # Получаем роль адреса для диагностики
            role_data = await loop.run_in_executor(None, hyperliquid_api.get_user_role, addr)
            role_info = ""
            if role_data:
                role = role_data.get('role', 'unknown')
                if role == 'user':
                    role_info = " (👤 обычный)"
                elif role == 'subAccount':
                    master = role_data.get('master', 'N/A')
                    role_info = f" (🔗 субаккаунт мастера {master[:10]}...)"
                elif role == 'vault':
                    role_info = " (🏦 vault)"
                elif role == 'agent':
                    role_info = " (🤖 агент)"
                else:
                    role_info = f" ({role})"
            else:
                role_info = " (❓ неизвестно)"
            
            # Проверяем спот-балансы
            spot_balances = user_data.get('balances', []) or []
            significant_balances = [b for b in spot_balances if float(b.get('total', 0)) > 100]  # > $100
            
            spot_info = ""
            if significant_balances:
                spot_coins = [b.get('coin', 'N/A') for b in significant_balances[:3]]
                spot_info = f"\n💰 Спот: {', '.join(spot_coins)}"
                if len(significant_balances) > 3:
                    spot_info += f" +{len(significant_balances)-3}"
            
            if style == 'mobile':
                parts.append(f"\n<pre><code>{addr}</code></pre>{role_info}\n📭 Нет открытых позиций{spot_info}")
            else:
                parts.append(f"\n<code>{addr}</code>{role_info}\n📭 Нет открытых позиций{spot_info}")
            continue

        # Собираем все позиции для пользователя
        lines = []
        total_unrealized = 0.0
        for pos in positions:
            position_info = pos.get('position', {}) or {}
            coin = position_info.get('coin', 'N/A')
            position_value = float(position_info.get('positionValue', 0) or 0)
            unrealized_pnl = float(position_info.get('unrealizedPnl', 0) or 0)
            entry_price = float(position_info.get('entryPx', 0) or 0)

            # Рассчитываем реальный USD-вход (объем позиции), а не цену за одну монету
            size_raw = (
                position_info.get('szi')
                or position_info.get('sz')
                or position_info.get('size')
                or position_info.get('positionSize')
                or position_info.get('rawPosSize')
            )
            try:
                size = float(size_raw) if size_raw is not None else 0.0
            except (TypeError, ValueError):
                size = 0.0
            # Если есть размер и цена входа — считаем объем на входе, иначе фолбэк на текущую оценку позиции
            entry_value_usd = abs(size) * entry_price if (size and entry_price) else position_value

            total_unrealized += unrealized_pnl

            # Направление определяем корректно
            direction_text, direction_emoji = hyperliquid_api.determine_position_direction(position_info, default_by_value=position_value)

            # Процент прибыли/убытка
            pnl_percent = (unrealized_pnl / entry_value_usd * 100) if entry_value_usd > 0 else 0
            
            lines.append(
                f"  {direction_emoji} <b>{coin}</b> {direction_text}\n"
                f"  💰 Вход: {hyperliquid_api.fmt_usd_compact(entry_value_usd)} | "
                f"PnL: {hyperliquid_api.fmt_usd_compact(unrealized_pnl, show_plus=True)} ({pnl_percent:+.1f}%)"
            )

        # Рассчитываем общую стоимость входа для процента
        total_entry_value = sum(
            abs(float(pos.get('position', {}).get('szi', 0) or 0)) * 
            float(pos.get('position', {}).get('entryPx', 0) or 0)
            for pos in positions if pos.get('position', {})
        )
        
        # Общий процент прибыли/убытка
        total_pnl_percent = (total_unrealized / total_entry_value * 100) if total_entry_value > 0 else 0
        
        summary_emoji = '🎉' if total_unrealized > 0 else ('😰' if total_unrealized < 0 else '😐')
        addr_repr = f"<pre><code>{addr}</code></pre>" if style == 'mobile' else f"<code>{addr}</code>"
        
        # Добавляем блок без объяснений (они будут в конце)
        parts.append(
            f"\n{addr_repr}\n" + "\n".join(lines) +
            f"\n\n📈 <b>Итого по позициям:</b>\n"
            f"💵 Общий вход: {hyperliquid_api.fmt_usd_compact(total_entry_value)}\n"
            f"{summary_emoji} Общий PnL: {hyperliquid_api.fmt_usd_compact(total_unrealized, show_plus=True)} ({total_pnl_percent:+.1f}%)"
        )

    # Добавляем объяснения только в конец
    explanation = "\n\nℹ️ <i>PnL = текущая прибыль/убыток (нереализованная)</i>\nℹ️ <i>Вход = стоимость позиции при открытии</i>"
    
    # Telegram ограничение ~4096 символов. Всегда отправляем по частям для надежности
    MAX_LEN = 3500  # Уменьшаем лимит для безопасности
    
    # Обновляем первоначальное сообщение заголовком
    header_text = parts[0] if parts else "<b>🧠 Позиции топ трейдеров</b>"
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=loading_msg.message_id,
        text=header_text,
        parse_mode='HTML'
    )

    # Отправляем каждый блок по адресу отдельным сообщением
    for i, block in enumerate(parts[1:]):
        if not block:
            continue
        
        # Проверяем длину блока
        if len(block) > MAX_LEN:
            # Если блок слишком длинный, обрезаем его
            block = block[:MAX_LEN-100] + "\n\n⚠️ <i>Данные обрезаны из-за ограничений Telegram</i>"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=block,
            parse_mode='HTML'
        )
    
    # Отправляем объяснения в конце
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=explanation,
        parse_mode='HTML'
    )

    return


# =============================
# Торговые команды
# =============================
async def set_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Разрешаем только в личных сообщениях
    if update.message.chat.type != 'private':
        warning_message = (
            "🔒 Приватный ключ отправляйте только в личном чате с ботом.\n\n"
            "Откройте личные сообщения с ботом\n\n"
            "Отправьте команду одним сообщением:\n"
            "<code>/set_key 0xВАШ_ПРИВАТНЫЙ_КЛЮЧ</code>\n\n"
            "Сообщение с ключом будет удалено автоматически."
        )
        await update.message.reply_html(warning_message, disable_web_page_preview=True)
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    message_id = update.message.message_id

    if not context.args:
        warning_message = (
            "🔒 Приватный ключ отправляйте только в личном чате с ботом.\n\n"
            "Откройте личные сообщения с ботом\n\n"
            "Отправьте команду одним сообщением:\n"
            "<code>/set_key 0xВАШ_ПРИВАТНЫЙ_КЛЮЧ</code>\n\n"
            "Сообщение с ключом будет удалено автоматически."
        )
        await update.message.reply_html(warning_message, disable_web_page_preview=True)
        return

    private_key = context.args[0]
    # Удаляем исходное сообщение с ключом
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        msg_deleted_text = "\n\n(Ваше сообщение с ключом было удалено)"
    except Exception:
        msg_deleted_text = "\n\n(Не удалось удалить ваше сообщение. Удалите его вручную!)"

    # Валидация формата ключа
    if not (private_key.startswith("0x") and len(private_key) == 66):
        await update.message.reply_text(f"❌ Ошибка. Неверный формат приватного ключа.{msg_deleted_text}")
        return

    # Шифруем и сохраняем (dual write: по user_id и по chat_id для обратной совместимости)
    try:
        address = _derive_address_from_private_key(private_key)
        # Ленивая инициализация ключа шифрования через trading.fernet
        fernet = trading.fernet
        encrypted_key = fernet.encrypt(private_key.encode())
        with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
            cursor = conn.cursor()
            # Новая запись по user_id
            cursor.execute(
                "INSERT OR REPLACE INTO user_trade_keys (user_id, address, encrypted_key) VALUES (?, ?, ?)",
                (user_id, address, encrypted_key),
            )
            # Старая запись по chat_id (legacy)
            cursor.execute(
                "INSERT OR REPLACE INTO user_trade_wallets (chat_id, address, encrypted_key) VALUES (?, ?, ?)",
                (chat_id, address, encrypted_key),
            )
            conn.commit()
        await update.message.reply_html(
            f"✅ Ваш торговый кошелек <code>{address}</code> успешно сохранен.{msg_deleted_text}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Произошла ошибка при обработке ключа: {e}{msg_deleted_text}")


async def _trade_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, is_buy: bool):
    # Подробные логи для диагностики + нормализация текста (NBSP и т.п.)
    try:
        user_id = getattr(update.effective_user, 'id', None)
        chat_id = getattr(update.effective_chat, 'id', None)
        raw_text = (getattr(update.message, 'text', '') or '')
        normalized_text = (raw_text
                           .replace('\u00A0', ' ')
                           .replace('\u202F', ' ')
                           .replace('\u2009', ' ')
                           .replace('\u2007', ' '))
        logger.info(f"/{action}: user_id={user_id} chat_id={chat_id} args={context.args} raw='{raw_text}' normalized='{normalized_text}'")
    except Exception:
        normalized_text = (getattr(update.message, 'text', '') or '')

    # Гибкий парсинг аргументов: допускаем нестандартные пробелы и упоминание бота
    working_args = list(context.args) if context.args else []
    if len(working_args) != 2:
        tokens = (normalized_text or '').strip().split()
        if tokens and tokens[0].startswith('/'):
            # Срезаем саму команду (/buy, /sell, возможно c @username)
            tokens = tokens[1:]
        working_args = tokens

    if len(working_args) < 2:
        await update.message.reply_html(
            f"Использование: <code>/{action} 🪙<b>монета</b> 💰<b>сумма</b></code>\nПример: <code>/{action} ETH 100</code>"
        )
        return

    coin = (working_args[0] or '').upper()
    try:
        amt_str = str(working_args[1]).replace('$', '').replace(',', '').strip()
        sz_usd = float(amt_str)
        if sz_usd <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Сумма должна быть положительным числом.")
        return

    side = "LONG" if is_buy else "SHORT"

    # Отправляем загрузочное сообщение максимально рано
    try:
        loading_msg = await update.message.reply_text(f"⏳ Открываю {side} по {coin} на ${sz_usd}...")
    except Exception as e:
        logger.error(f"Не удалось отправить loading_msg для {action}: {e}")
        try:
            await update.message.reply_text("⏳ Выполняю операцию...")
        except Exception as e2:
            logger.debug(f"Не удалось отправить fallback loading_msg: {e2}")
        # Попытаемся продолжить без loading_msg
        loading_msg = None

    # Выполнение в пуле потоков с перехватом ошибок
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            trading.execute_trade_action,
            update.effective_user.id,
            update.effective_chat.id,
            "market_open",
            {"coin": coin, "is_buy": is_buy, "sz_usd": sz_usd},
        )
    except Exception as e:
        logger.error(f"Ошибка run_in_executor для {action}: {e}", exc_info=True)
        result = {"success": False, "error": str(e)}

    # Возврат пользователю
    text = (
        f"✅ Успешно! {result['data']}" if result.get("success")
        else f"❌ Ошибка при открытии позиции: {result.get('error', 'Unknown')}"
    )
    try:
        if loading_msg is not None:
            await loading_msg.edit_text(text)
        else:
            await update.message.reply_text(text)
    except Exception as e:
        logger.error(f"Не удалось отправить итоговое сообщение для {action}: {e}")


async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _trade_command_handler(update, context, action="sell", is_buy=False)


async def close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_html("Использование: <code>/close 🪙<b>монета</b></code>\nПример: <code>/close ETH</code>")
        return

    coin = context.args[0].upper()
    loading_msg = await update.message.reply_text(f"⏳ Закрываю позицию по {coin}...")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        trading.execute_trade_action,
        update.effective_user.id,
        update.effective_chat.id,
        "market_close",
        {"coin": coin},
    )

    if result.get("success"):
        await loading_msg.edit_text(f"✅ Успешно! {result['data']}")
    else:
        await loading_msg.edit_text(f"❌ Ошибка при закрытии позиции: {result.get('error', 'Unknown')}")


async def leverage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Логи вызова + нормализация нестандартных пробелов
    try:
        user_id = getattr(update.effective_user, 'id', None)
        chat_id = getattr(update.effective_chat, 'id', None)
        raw_text = (getattr(update.message, 'text', '') or '')
        normalized_text = (raw_text
                           .replace('\u00A0', ' ')
                           .replace('\u202F', ' ')
                           .replace('\u2009', ' ')
                           .replace('\u2007', ' '))
        logger.info(f"/leverage: user_id={user_id} chat_id={chat_id} args={context.args} raw='{raw_text}' normalized='{normalized_text}'")
    except Exception:
        normalized_text = (getattr(update.message, 'text', '') or '')

    # Гибкий парсинг аргументов
    args = list(context.args) if context.args else []
    if len(args) != 2:
        tokens = (normalized_text or '').strip().split()
        if tokens and tokens[0].startswith('/'):
            tokens = tokens[1:]
        args = tokens

    if len(args) < 2:
        await update.message.reply_html(
            "Использование: <code>/leverage 🪙<b>монета</b> ⚡<b>плечо</b></code>\nПример: <code>/leverage ETH 20</code>"
        )
        return

    coin = (args[0] or '').upper()
    try:
        lev_str = str(args[1]).replace('x', '').replace('X', '').strip()
        leverage_val = int(lev_str)
        if not (1 <= leverage_val <= 50):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Плечо должно быть целым числом (например, 10 или 25).")
        return

    try:
        loading_msg = await update.message.reply_text(f"⏳ Устанавливаю плечо x{leverage_val} для {coin}...")
    except Exception as e:
        logger.error(f"Не удалось отправить loading_msg для leverage: {e}")
        loading_msg = None

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            trading.execute_trade_action,
            update.effective_user.id,
            update.effective_chat.id,
            "update_leverage",
            {"coin": coin, "leverage": leverage_val},
        )
    except Exception as e:
        logger.error(f"Ошибка run_in_executor для leverage: {e}", exc_info=True)
        result = {"success": False, "error": str(e)}

    text = (
        f"✅ Успешно! {result['data']}" if result.get("success")
        else f"❌ Ошибка при установке плеча: {result.get('error', 'Unknown')}"
    )
    try:
        if loading_msg is not None:
            await loading_msg.edit_text(text)
        else:
            await update.message.reply_text(text)
    except Exception as e:
        logger.error(f"Не удалось отправить итоговое сообщение для leverage: {e}")


# Новая команда: /cancel — отмена лимитного ордера по монете и ID ордера
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usage = (
        "<b>⚠️ Укажите монету и ID ордера</b>\n\n"
        "Использование: /cancel <code>COIN</code> <code>ORDER_ID</code>\n"
        "Пример: /cancel ETH 123456789"
    )
    if len(context.args) != 2:
        await update.message.reply_html(usage)
        return

    coin = context.args[0].upper()
    oid_str = context.args[1]
    try:
        oid = int(oid_str)
        if oid <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_html(f"Некорректный ID ордера: <code>{oid_str}</code>\n\n" + usage)
        return

    loading_msg = await update.message.reply_text(f"⏳ Отменяю ордер #{oid} по {coin}...")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        trading.execute_trade_action,
        update.effective_user.id,
        update.effective_chat.id,
        "cancel_order",
        {"coin": coin, "oid": oid},
    )

    if result.get("success"):
        await loading_msg.edit_text(f"✅ Успешно! {result['data']}")
    else:
        await loading_msg.edit_text(f"❌ Ошибка при отмене ордера: {result.get('error', 'Unknown')}")

async def set_key_group_warning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отвечает в группах на /set_key предупреждением и предлагает перейти в ЛС."""
    warning_message = (
        "️️⚠️ <b>ОПАСНО!</b>\n\n"
        "Команда <code>/set_key</code> работает <b>только</b> в личном чате с ботом.\n\n"
        "Откройте личные сообщения с ботом\n\n"
        "Причина: в группах сообщение может успеть увидеть кто-то ещё или уйти в пуш-уведомления."
    )
    try:
        await update.message.reply_html(warning_message, disable_web_page_preview=True)
    except Exception as e:
        # Игнорируем ошибки отправки предупреждения
        logger.debug(f"Не удалось отправить предупреждение в группе: {e}")
    # Пытаемся удалить оригинальное сообщение пользователя, чтобы минимизировать риск утечки
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception as e:
        logger.debug(f"Не удалось удалить исходное сообщение в группе: {e}")


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _trade_command_handler(update, context, action="buy", is_buy=True)


async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Алиас /order: создание рыночных и лимитных ордеров и управление (cancel, close, leverage)."""
    # Делегируем парсинг и выполнение в обработчик /order
    await order(update, context)


async def order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание рыночных и лимитных ордеров.

    Примеры:
    /order market buy BTC 1000 — купить на $1000
    /order market sell ETH 250 — продать на $250
    /order limit buy BTC 0.5 @ 25000 — лимитная покупка 0.5 BTC по $25000
    /order limit sell ETH 1 @ 3500 ioc — лимитная продажа 1 ETH по $3500 (IOC)
    /order limit buy SOL 10 @ 140 alo reduce — разместить только как мейкер + reduce-only
    """
    args = context.args or []

    usage = (
        "<b>Использование:</b>\n"
        "• /order market buy|sell COIN USD\n"
        "• /order limit buy|sell COIN SIZE @ PRICE [gtc|ioc|alo] [reduce]\n\n"
        "Примеры:\n"
        "• /order market buy BTC 1000\n"
        "• /order limit sell ETH 1 @ 3500 ioc\n"
        "• /order limit buy SOL 10 @ 140 alo reduce"
    )

    if not args:
        await update.message.reply_html(usage)
        return

    mode = args[0].lower()

    # Рыночный ордер в USD
    if mode == 'market':
        if len(args) < 4:
            await update.message.reply_html(usage)
            return
        side = args[1].lower()
        if side in ('buy', 'long', 'покупка', 'купить'):
            is_buy = True
        elif side in ('sell', 'short', 'продажа', 'продать'):
            is_buy = False
        else:
            await update.message.reply_html("Укажите сторону: buy или sell\n\n" + usage)
            return
        coin = args[2].upper()
        try:
            sz_usd = float(args[3].replace(',', '.'))
            if sz_usd <= 0:
                raise ValueError
        except Exception:
            await update.message.reply_html("Некорректная сумма в USD. Пример: /order market buy BTC 1000")
            return

        loading_msg = await update.message.reply_html("<b>⏳ Размещение рыночного ордера...</b>")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            trading.execute_trade_action,
            update.effective_user.id,
            update.effective_chat.id,
            'market_open',
            {"coin": coin, "is_buy": is_buy, "sz_usd": sz_usd}
        )

        text = result.get('data') if result.get('success') else f"❌ Ошибка: {result.get('error', 'неизвестно')}"
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=loading_msg.message_id,
            text=text,
            parse_mode='HTML'
        )
        return

    # Лимитный ордер в размере монет
    if mode == 'limit':
        if len(args) < 5:
            await update.message.reply_html(usage)
            return
        side = args[1].lower()
        if side in ('buy', 'long', 'покупка', 'купить'):
            is_buy = True
        elif side in ('sell', 'short', 'продажа', 'продать'):
            is_buy = False
        else:
            await update.message.reply_html("Укажите сторону: buy или sell\n\n" + usage)
            return

        coin = args[2].upper()

        # Разбор размера и цены: требуем явное наличие символа '@'
        size_token = args[3]
        price_token = None
        remaining_tokens_start = 4
        saw_at_explicit = False
        if '@' in size_token:
            saw_at_explicit = True
            parts = size_token.split('@', 1)
            size_token = parts[0]
            price_token = parts[1]
        else:
            if len(args) > 4 and args[4] == '@':
                saw_at_explicit = True
                price_token = args[5] if len(args) > 5 else None
                remaining_tokens_start = 6
            elif len(args) > 4 and args[4].startswith('@'):
                saw_at_explicit = True
                price_token = args[4][1:]
                remaining_tokens_start = 5

        if not saw_at_explicit:
            await update.message.reply_html("Отсутствует символ '@' между размером и ценой.\nПример: /order limit buy BTC 0.5 @ 25000")
            return
        if price_token is None or price_token == "":
            await update.message.reply_html("Не указана цена после '@'.\nПример: /order limit buy BTC 0.5 @ 25000")
            return

        try:
            sz = float(size_token.replace(',', '.'))
            limit_px = float(price_token.replace(',', '.'))
            if sz <= 0 or limit_px <= 0:
                raise ValueError
        except Exception:
            await update.message.reply_html("Некорректные размер/цена. Пример: /order limit buy BTC 0.5 @ 25000")
            return

        # Параметры: tif и reduce_only
        tif = 'Gtc'
        reduce_only = False
        unknown_flags = []
        for tok in args[remaining_tokens_start:]:
            low = tok.lower()
            if low in ('gtc', 'ioc', 'alo'):
                tif = low.capitalize()
            elif low in ('reduce', 'reduce_only', 'ro', '-r', '--reduce'):
                reduce_only = True
            else:
                unknown_flags.append(tok)
        if unknown_flags:
            await update.message.reply_html(
                "Неизвестные параметры: " + ", ".join(unknown_flags) + "\n" 
                "Допустимые: gtc, ioc, alo, reduce"
            )
            return

        loading_msg = await update.message.reply_html("<b>⏳ Размещение лимитного ордера...</b>")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            trading.execute_trade_action,
            update.effective_user.id,
            update.effective_chat.id,
            'limit_order',
            {"coin": coin, "is_buy": is_buy, "sz": sz, "limit_px": limit_px, "reduce_only": reduce_only, "tif": tif}
        )

        text = result.get('data') if result.get('success') else f"❌ Ошибка: {result.get('error', 'неизвестно')}"
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=loading_msg.message_id,
            text=text,
            parse_mode='HTML'
        )
        return

    # Если режим не распознан
    await update.message.reply_html("Неизвестный режим. Укажите market или limit.\n\n" + usage)

async def wallet_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать активность кошелька"""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите адрес кошелька!\n\n"
            "Использование: /wallet_activity адрес"
        )
        return
    
    wallet_address = context.args[0]
    
    # Проверяем, отслеживается ли кошелек
    user_id = update.effective_chat.id
    wallets = database.get_wallets_for_user(user_id)
    
    if wallet_address not in wallets:
        await update.message.reply_html(
            "❌ Кошелек не отслеживается!\n\n"
            "Добавьте его командой: /add адрес"
        )
        return
    
    # Отправляем сообщение о загрузке
    loading_message = await update.message.reply_html(
        f"⏳ <b>Загружаю активность кошелька...</b>\n"
        f"🔗 <code>{wallet_address}</code>"
    )
    
    try:
        # Получаем историю сделок
        from hyperliquid_api import get_user_fills, format_user_fills_message
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        fills = get_user_fills(wallet_address, limit=50)  # Увеличиваем лимит
        
        if fills:
            # Получаем текущую страницу из контекста или устанавливаем 0
            page = int(context.user_data.get(f'page_{wallet_address}', 0))
            
            # Форматируем сообщение с историей сделок
            activity_message, has_prev, has_next = format_user_fills_message(fills, wallet_address, page)
            
            # Создаем inline клавиатуру для навигации
            keyboard = []
            nav_buttons = []
            
            if has_prev:
                nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"wallet_prev_{wallet_address}_{page-1}"))
            
            if has_next:
                nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"wallet_next_{wallet_address}_{page+1}"))
            
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            # Добавляем кнопку обновления
            keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data=f"wallet_refresh_{wallet_address}_{page}")])
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            # Обновляем сообщение с результатами
            await loading_message.edit_text(
                activity_message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        else:
            await loading_message.edit_text(
                f"📊 <b>Активность кошелька</b>\n"
                f"🔗 <code>{wallet_address}</code>\n\n"
                f"📭 История сделок пуста или недоступна\n\n"
                f"💡 Возможные причины:\n"
                f"• Кошелек не совершал сделки\n"
                f"• Адрес указан неверно\n"
                f"• Временные проблемы с API",
                parse_mode='HTML'
            )
            
    except Exception as e:
        logger.error(f"Error getting wallet activity for {wallet_address}: {e}")
        await loading_message.edit_text(
            f"❌ <b>Ошибка при получении активности</b>\n"
            f"🔗 <code>{wallet_address}</code>\n\n"
            f"⚠️ Не удалось загрузить данные\n"
            f"Попробуйте позже или проверьте адрес",
            parse_mode='HTML'
        )

async def wallet_navigation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик навигации по истории сделок кошелька"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith('wallet_'):
        parts = data.split('_')
        if len(parts) == 2:  # wallet_address - возврат к основной странице
            wallet_address = parts[1]
            # Вызываем функцию отображения основной страницы кошелька
            await positions_command_with_address(update, context, wallet_address)
            return
        elif len(parts) == 4:  # wallet_action_address_page - навигация по истории
            action = parts[1]  # prev, next, refresh
            wallet_address = parts[2]
            page = int(parts[3])
        else:
            return  # Неправильный формат
        
        # Сохраняем текущую страницу в контексте
        context.user_data[f'page_{wallet_address}'] = page
        
        try:
            from hyperliquid_api import get_user_fills, format_user_fills_message
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            
            fills = get_user_fills(wallet_address, limit=50)
            
            if fills:
                # Форматируем сообщение с историей сделок
                activity_message, has_prev, has_next = format_user_fills_message(fills, wallet_address, page)
                
                # Создаем inline клавиатуру для навигации
                keyboard = []
                nav_buttons = []
                
                if has_prev:
                    nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"wallet_prev_{wallet_address}_{page-1}"))
                
                if has_next:
                    nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"wallet_next_{wallet_address}_{page+1}"))
                
                if nav_buttons:
                    keyboard.append(nav_buttons)
                
                # Добавляем кнопку обновления
                keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data=f"wallet_refresh_{wallet_address}_{page}")])
                
                reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
                
                # Обновляем сообщение с обработкой ошибки "Message is not modified"
                try:
                    await query.edit_message_text(
                        activity_message,
                        reply_markup=reply_markup,
                        parse_mode='HTML'
                    )
                except Exception as e:
                    if "Message is not modified" in str(e):
                        # Если сообщение не изменилось, просто игнорируем ошибку
                        logger.debug("Сообщение не изменилось, пропускаем обновление")
                    else:
                        # Если другая ошибка, логируем её
                        logger.error(f"Error updating message: {e}")
            else:
                await query.edit_message_text(
                    f"📊 <b>Активность кошелька</b>\n"
                    f"🔗 <code>{wallet_address}</code>\n\n"
                    f"📭 История сделок пуста или недоступна",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            logger.error(f"Error in wallet navigation callback: {e}")
            await query.edit_message_text(
                f"❌ <b>Ошибка при загрузке данных</b>\n"
                f"🔗 <code>{wallet_address}</code>\n\n"
                f"⚠️ Попробуйте позже",
                parse_mode='HTML'
            )

async def leaderboard_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Лидерборд за день"""
    await update.message.reply_text(
        "🏆 <b>Топ трейдеров за день</b>\n\n"
        "🚧 <i>Функция в разработке</i>\n\n"
        "📊 Будет показывать:\n"
        "• Топ-10 трейдеров по PnL\n"
        "• Объем торгов\n"
        "• Количество сделок\n"
        "• ROI за период\n\n"
        "⏳ Следите за обновлениями!",
        parse_mode='HTML'
    )

async def leaderboard_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Лидерборд за неделю"""
    await update.message.reply_text(
        "🏆 <b>Топ трейдеров за неделю</b>\n\n"
        "🚧 <i>Функция в разработке</i>\n\n"
        "📊 Будет показывать:\n"
        "• Топ-10 трейдеров по PnL\n"
        "• Объем торгов\n"
        "• Количество сделок\n"
        "• ROI за период\n\n"
        "⏳ Следите за обновлениями!",
        parse_mode='HTML'
    )

async def leaderboard_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Лидерборд за месяц"""
    await update.message.reply_text(
        "🏆 <b>Топ трейдеров за месяц</b>\n\n"
        "🚧 <i>Функция в разработке</i>\n\n"
        "📊 Будет показывать:\n"
        "• Топ-10 трейдеров по PnL\n"
        "• Объем торгов\n"
        "• Количество сделок\n"
        "• ROI за период\n\n"
        "⏳ Следите за обновлениями!",
        parse_mode='HTML'
    )


async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает открытые ордера по адресу с пагинацией."""
    address = clean_address(context.args)
    if not address:
        await update.message.reply_html(
            "<b>⚠️ Укажите адрес</b>\n\n"
            "Использование: /orders адрес"
        )
        return

    loading_msg = await update.message.reply_html(
        "<b>⏳ Загружаю открытые ордера...</b>"
    )

    loop = asyncio.get_running_loop()
    open_orders = await loop.run_in_executor(None, hyperliquid_api.get_open_orders, address)

    # Формируем первую страницу
    header = f"💼 Адрес: <code>{address}</code>\n"
    orders_text = hyperliquid_api.format_open_orders(open_orders, page=1)
    if not orders_text:
        try:
            count = len(open_orders) if isinstance(open_orders, list) else 0
        except Exception:
            count = 0
        orders_text = f"\n<b>📑 Открытые ордера (всего {count}):</b>\n—"

    message = header + orders_text

    # Пагинация: по 5 ордеров на страницу
    try:
        total = len(open_orders) if isinstance(open_orders, list) else 0
    except Exception:
        total = 0
    orders_per_page = 5
    total_pages = (total + orders_per_page - 1) // orders_per_page if total > 0 else 1

    keyboard = []
    if total_pages > 1:
        keyboard.append([
            InlineKeyboardButton("1/" + str(total_pages), callback_data=f"orders_page_1_{address}"),
            InlineKeyboardButton("Вперед ▶️", callback_data=f"orders_page_2_{address}")
        ])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=loading_msg.message_id,
        text=message,
        parse_mode='HTML',
        reply_markup=reply_markup
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Красивый вывод баланса кошелька"""
    # Диагностика входа и нормализация пробелов
    try:
        raw_text = (getattr(update.message, 'text', '') or '')
        normalized_text = (raw_text
                           .replace('\u00A0', ' ')
                           .replace('\u202F', ' ')
                           .replace('\u2009', ' ')
                           .replace('\u2007', ' '))
        logger.info(f"/balance raw='{raw_text}' normalized='{normalized_text}' args={context.args}")
    except Exception as e:
        logger.debug(f"/balance logging normalization failed: {e}")

    address = clean_address(context.args)
    if not address:
        await update.message.reply_html(
            "<b>⚠️ Укажите адрес</b>\n\n"
            "🔍 Используйте: /balance <code>адрес</code>"
        )
        return

    loading_msg = await update.message.reply_html(
        "<b>⏳ Загрузка баланса...</b>"
    )

    # Предпочтение формата пользователя
    chat_id = update.effective_chat.id
    pref = database.get_user_format_preference(chat_id)
    style = 'mobile' if pref == 'mobile' else 'desktop'

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, hyperliquid_api.get_user_positions_with_sdk, address)
    if isinstance(data, dict):
        try:
            logger.info(f"/balance fetched data keys: {list(data.keys())}")
        except Exception as e:
            logger.debug(f"/balance logging normalization failed (keys): {e}")
    else:
        logger.info(f"/balance fetched data type: {type(data)}")

    message = hyperliquid_api.format_balance_message(address, data, style=style)

    # Если деривативные данные пустые — добавим полезные детали (роль и др.)
    try:
        positions_empty = not (isinstance(data, dict) and data.get('assetPositions'))
    except Exception:
        positions_empty = True

    if not data or positions_empty:
        extras: list[str] = []

        # Роль пользователя (мастер/субаккаунт/вальт/агент)
        try:
            role_data = await loop.run_in_executor(None, hyperliquid_api.get_user_role, address)
            if isinstance(role_data, dict):
                role = role_data.get('role') or role_data.get('type') or 'unknown'
                role_human = ''
                if role == 'user':
                    role_human = '👤 Обычный пользователь'
                elif role == 'subAccount':
                    master = role_data.get('master', '')
                    master_short = (master[:10] + '...') if master else 'N/A'
                    role_human = f"🔗 Субаккаунт мастера {master_short}"
                elif role == 'vault':
                    role_human = '🏦 Vault аккаунт'
                elif role == 'agent':
                    role_human = '🤖 Агент'
                else:
                    role_human = f"❓ {role}"
                extras.append(f"👤 Роль адреса: {role_human}")
        except Exception as e:
            logger.warning(f"/balance get_user_role failed for {address}: {e}")

        # Добавим спот-баланс
        spot_info = ""
        try:
            spot_balances = await loop.run_in_executor(None, hyperliquid_api.get_spot_balances, address)
            if spot_balances:
                spot_info = f"\n\n💰 Спот активы: {hyperliquid_api.format_spot_balances(spot_balances)}"
        except Exception as e:
            logger.warning(f"/balance spot balances fetch failed for {address}: {e}")

        if extras:
            message = f"{message}\n\n" + "\n".join(extras)
        if spot_info:
            message += spot_info

    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=loading_msg.message_id,
        text=message,
        parse_mode='HTML'
    )


# Обработчик навигации по частям сообщения
async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок навигации для длинных сообщений."""
    query = update.callback_query
    await query.answer()
    
    if 'message_parts' not in context.user_data:
        await query.edit_message_text("❌ Данные навигации не найдены")
        return
    
    parts = context.user_data['message_parts']
    current = context.user_data.get('current_part', 0)
    
    if query.data == "nav_prev":
        current = max(0, current - 1)
    elif query.data == "nav_next":
        current = min(len(parts) - 1, current + 1)
    elif query.data == "nav_info":
        return  # Просто информационная кнопка
    
    context.user_data['current_part'] = current
    
    # Создаем кнопки навигации
    keyboard = []
    if len(parts) > 1:
        keyboard.append([
            InlineKeyboardButton("◀️ Назад", callback_data="nav_prev"),
            InlineKeyboardButton(f"{current + 1}/{len(parts)}", callback_data="nav_info"),
            InlineKeyboardButton("Вперед ▶️", callback_data="nav_next")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    await query.edit_message_text(
        text=parts[current],
        parse_mode='HTML',
        reply_markup=reply_markup
    )


async def handle_orders_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик навигации по страницам ордеров"""
    query = update.callback_query
    await query.answer()
    
    # Парсим callback_data: orders_page_{page}_{address}
    callback_parts = query.data.split('_')
    if len(callback_parts) < 4:
        return
    
    page = int(callback_parts[2])
    address = '_'.join(callback_parts[3:])  # Адрес может содержать подчеркивания
    
    # Получаем данные позиций и ордеров
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, hyperliquid_api.get_user_positions_with_sdk, address)
    open_orders = await loop.run_in_executor(None, hyperliquid_api.get_open_orders, address)
    
    # Формируем сообщение только с ордерами (без позиций на страницах ордеров)
    message = f"💼 Адрес: <code>{address}</code>"
    orders_text = hyperliquid_api.format_open_orders(open_orders, page=page)
    if not orders_text:
        try:
            count = len(open_orders) if isinstance(open_orders, list) else 0
        except Exception:
            count = 0
        orders_text = f"\n<b>📑 Открытые ордера (страница {page}, всего {count}):</b>\n—"
    message += "\n" + orders_text
    
    # Добавляем роль адреса если нужно
    positions_empty = not (data and (data.get('assetPositions') or []))
    orders_empty = (not open_orders or len(open_orders) == 0)
    if positions_empty or orders_empty:
        role_data = await loop.run_in_executor(None, hyperliquid_api.get_user_role, address)
        role_info = ""
        if role_data:
            role = role_data.get('role', 'unknown')
            if role == 'user':
                role_info = "👤 обычный"
            elif role == 'subAccount':
                master = role_data.get('master', 'N/A')
                role_info = f"🔗 субаккаунт мастера {master[:10]}..."
            elif role == 'vault':
                role_info = "🏦 vault"
            else:
                role_info = f"❓ {role}"
        
        spot_info = ""
        try:
            spot_balances = await loop.run_in_executor(None, hyperliquid_api.get_spot_balances, address)
            if spot_balances:
                spot_info = f"\n\n💰 Спот активы: {hyperliquid_api.format_spot_balances(spot_balances)}"
        except Exception as e:
            logger.warning(f"Ошибка получения спот баланса для {address}: {e}")
        
        message += (
            f"\n\n👤 Роль адреса: {role_info}"
            f"{spot_info}\n"
            f"💡 Подсказка: лимитные заявки отображаются в разделе открытых ордеров. "
            f"Если маркет-ордер был исполнен и позиция сразу закрылась, активных позиций может не быть."
        )
    
    # Создаем кнопки навигации
    keyboard = []
    if open_orders and len(open_orders) > 0:
        total_pages = (len(open_orders) + 4) // 5
        nav_buttons = []
        
        # Кнопка "Назад" для возврата к основной странице (без ордеров)
        if page == 2:
            nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"wallet_{address}"))
        elif page > 2:
            nav_buttons.append(InlineKeyboardButton(f"◀️ Стр {page-1}", callback_data=f"orders_page_{page-1}_{address}"))
        
        # Показываем текущую страницу
        nav_buttons.append(InlineKeyboardButton(f"Стр {page}/{total_pages}", callback_data="orders_info"))
        
        # Кнопка "Вперед" если есть еще страницы
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton(f"Стр {page+1} ▶️", callback_data=f"orders_page_{page+1}_{address}"))
        
        keyboard.append(nav_buttons)
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    await query.edit_message_text(
        text=message,
        parse_mode='HTML',
        reply_markup=reply_markup
    )


# Команда для выбора предпочитаемого формата вывода
async def format_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/format desktop|mobile — сохраняет предпочтение вывода для текущего чата."""
    if not context.args or context.args[0].lower() not in ('desktop', 'mobile'):
        await update.message.reply_html(
            "Использование: <code>/format desktop</code> или <code>/format mobile</code>\n\n"
            "desktop — аккуратный вид (в одну строку),\nmobile — удобное копирование адреса (<pre><code>блок</code></pre>)."
        )
        return
    pref = context.args[0].lower()
    database.set_user_format_preference(update.effective_chat.id, pref)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Справочная информация о терминах и различиях."""
    help_message = """<b>📚 СПРАВОЧНАЯ ИНФОРМАЦИЯ</b>

<b>🔍 Основные различия:</b>

<b>🔴 Открытые сделки (История сделок)</b>
• Это <b>завершенные</b> транзакции покупки/продажи
• Показывает историю всех выполненных операций
• Включает: время сделки, размер, цену, направление (BUY/SELL)
• Это уже <b>произошедшие</b> события в прошлом

<b>📊 Открытые позиции</b>
• Это <b>текущие</b> активные позиции на рынке
• Показывает что у вас есть прямо сейчас
• Включает: размер позиции, направление (LONG/SHORT), нереализованную прибыль/убыток, цену входа, плечо
• Это ваши <b>действующие</b> инвестиции

<b>📑 Открытые ордера</b>
• Это <b>ожидающие</b> исполнения заявки
• Показывает ордера, которые еще не выполнились
• Включает: тип ордера (Buy/Sell), размер, лимитную цену, статус
• Это ваши <b>будущие</b> сделки, ожидающие исполнения

<b>🎯 Простая аналогия:</b>
• <i>Сделки</i> = что вы уже купили/продали (история)
• <i>Позиции</i> = что у вас есть сейчас (настоящее)
• <i>Ордера</i> = что вы хотите купить/продать (будущее)

<b>🔧 Полезные команды:</b>
/positions адрес — посмотреть текущие позиции
/wallet_activity адрес — история сделок
/orders адрес — открытые ордера
/order market|limit — выставить ордер (напр.: /order market buy BTC 1000; /order limit sell ETH 1 @ 3500)
/balance адрес — показать баланс кошелька и эквити
/format desktop|mobile — выбрать формат вывода для чата
/buy|/sell COIN USD — рыночная покупка/продажа на сумму в USD
/close COIN — закрыть открытую позицию по рынку
/leverage COIN X — установить плечо для COIN
/cancel COIN ORDER_ID — отменить лимитный ордер по id
/set_key приватный_ключ — сохранить торговый ключ (отправлять ТОЛЬКО в ЛС с ботом)
"""
    
    await update.message.reply_html(help_message)