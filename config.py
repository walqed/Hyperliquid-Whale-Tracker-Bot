# config.py
import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

# --- ОСНОВНЫЕ НАСТРОЙКИ ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Порог по умолчанию для уведомлений о сделках в USD
DEFAULT_TRADE_THRESHOLD_USD = 100000.0
# --- НАСТРОЙКИ БАЗЫ ДАННЫХ ---
DB_FILE = "whale_bot_final.db"