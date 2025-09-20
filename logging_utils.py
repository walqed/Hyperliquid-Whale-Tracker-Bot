import logging
import os
from datetime import datetime

# Создаем директорию для логов если её нет
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Настройка логгера
logger = logging.getLogger('whale_bot')
logger.setLevel(logging.INFO)

# Создаем форматтер
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Файловый обработчик
file_handler = logging.FileHandler('whale_bot.log', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

# Консольный обработчик
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# Добавляем обработчики к логгеру
if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# Предотвращаем дублирование логов
logger.propagate = False