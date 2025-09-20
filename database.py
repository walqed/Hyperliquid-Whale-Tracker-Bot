# database.py
import sqlite3
import logging
from config import DB_FILE, DEFAULT_TRADE_THRESHOLD_USD

logger = logging.getLogger(__name__)


def init_db():
    """Инициализирует базу данных и создаёт таблицы, если их нет."""
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS tracked_wallets (chat_id INTEGER NOT NULL, wallet_address TEXT NOT NULL, PRIMARY KEY (chat_id, wallet_address))")
        cursor.execute("CREATE TABLE IF NOT EXISTS user_settings (chat_id INTEGER PRIMARY KEY, threshold REAL NOT NULL)")
        # Миграция: добавить колонку order_threshold, если её нет
        cursor.execute("PRAGMA table_info(user_settings)")
        cols = [row[1] for row in cursor.fetchall()]
        if 'order_threshold' not in cols:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN order_threshold REAL")
        # Миграция: добавить колонку format_preference (desktop|mobile)
        cursor.execute("PRAGMA table_info(user_settings)")
        cols = [row[1] for row in cursor.fetchall()]
        if 'format_preference' not in cols:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN format_preference TEXT")
        # Новая таблица для безопасного хранения торгового ключа пользователя
        cursor.execute("CREATE TABLE IF NOT EXISTS user_trade_wallets (chat_id INTEGER PRIMARY KEY, address TEXT, encrypted_key BLOB)")
        # Хранение торгового ключа по user_id (для использования в группах каждым участником)
        cursor.execute("CREATE TABLE IF NOT EXISTS user_trade_keys (user_id INTEGER PRIMARY KEY, address TEXT, encrypted_key BLOB)")
        conn.commit()
    logger.info("База данных успешно инициализирована.")


def add_wallet_to_db(chat_id: int, address: str):
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO tracked_wallets (chat_id, wallet_address) VALUES (?, ?)", (chat_id, address.lower()))
        conn.commit()


def remove_wallet_from_db(chat_id: int, address: str):
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tracked_wallets WHERE chat_id = ? AND wallet_address = ?", (chat_id, address.lower()))
        conn.commit()


def get_wallets_for_user(chat_id: int) -> list:
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet_address FROM tracked_wallets WHERE chat_id = ?", (chat_id,))
        return [row[0] for row in cursor.fetchall()]


def get_all_unique_wallets() -> set:
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT wallet_address FROM tracked_wallets")
        return {row[0] for row in cursor.fetchall()}


def get_users_tracking_wallet(address: str) -> list:
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        # Сначала пробуем точное совпадение
        cursor.execute("SELECT chat_id FROM tracked_wallets WHERE wallet_address = ?", (address.lower(),))
        users = [row[0] for row in cursor.fetchall()]
        
        # Если точного совпадения нет, ищем по началу адреса (для сокращенных адресов)
        if not users and len(address) >= 10:
            cursor.execute("SELECT chat_id FROM tracked_wallets WHERE wallet_address LIKE ?", (address.lower() + '%',))
            users = [row[0] for row in cursor.fetchall()]
            
        return users


def set_user_threshold(chat_id: int, threshold: float):
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO user_settings (chat_id, threshold) VALUES (?, ?)", (chat_id, threshold))
        conn.commit()


def get_user_threshold(chat_id: int) -> float:
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT threshold FROM user_settings WHERE chat_id = ?", (chat_id,))
        result = cursor.fetchone()
        return result[0] if result else DEFAULT_TRADE_THRESHOLD_USD

# --- Отдельный порог для ордеров ---

def set_user_order_threshold(chat_id: int, threshold: float):
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        # Гарантируем наличие строки с NOT NULL threshold
        cursor.execute(
            "INSERT OR IGNORE INTO user_settings (chat_id, threshold) VALUES (?, ?)",
            (chat_id, DEFAULT_TRADE_THRESHOLD_USD)
        )
        # Обновляем только order_threshold
        cursor.execute(
            "UPDATE user_settings SET order_threshold = ? WHERE chat_id = ?",
            (threshold, chat_id)
        )
        conn.commit()


def get_user_order_threshold(chat_id: int) -> float:
    """Возвращает порог для ордеров. Если не задан — возвращает общий threshold или дефолт."""
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT threshold, order_threshold FROM user_settings WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if not row:
            return DEFAULT_TRADE_THRESHOLD_USD
        base_threshold, order_threshold = row[0], row[1]
        if order_threshold is not None and order_threshold > 0:
            return order_threshold
        # Фоллбек на общий порог
        return base_threshold if base_threshold and base_threshold > 0 else DEFAULT_TRADE_THRESHOLD_USD


# --- Форматирование сообщений (desktop|mobile) ---

def set_user_format_preference(chat_id: int, preference: str):
    """preference: 'desktop' или 'mobile'"""
    preference = (preference or '').lower()
    if preference not in ('desktop', 'mobile'):
        preference = 'desktop'
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        # Проверяем, существует ли запись
        cursor.execute("SELECT chat_id FROM user_settings WHERE chat_id = ?", (chat_id,))
        exists = cursor.fetchone()
        
        if exists:
            # Обновляем существующую запись
            cursor.execute(
                "UPDATE user_settings SET format_preference = ? WHERE chat_id = ?",
                (preference, chat_id)
            )
        else:
            # Создаем новую запись с дефолтным threshold
            cursor.execute(
                "INSERT INTO user_settings (chat_id, threshold, format_preference) VALUES (?, ?, ?)",
                (chat_id, DEFAULT_TRADE_THRESHOLD_USD, preference)
            )
        conn.commit()


def get_user_format_preference(chat_id: int) -> str:
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT format_preference FROM user_settings WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        pref = (row[0] if row else None) or 'desktop'
        return pref