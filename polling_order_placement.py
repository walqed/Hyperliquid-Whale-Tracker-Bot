#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пример размещения лимитных ордеров через polling
Использует существующую функцию execute_trade_action для размещения ордеров
"""

import time
import logging
from trading import execute_trade_action
from hyperliquid_api import get_open_orders, get_all_mids
import config

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('polling_orders.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Чат/пользователь по умолчанию для запуска из CLI
try:
    CHAT_ID_FOR_TRADING = next(iter(config.ADMIN_TELEGRAM_IDS or config.ALLOWED_TELEGRAM_IDS))
except Exception:
    CHAT_ID_FOR_TRADING = 0

def place_limit_order(coin, is_buy, sz, limit_px, reduce_only=False, tif="Gtc"):
    """
    Размещает лимитный ордер через API
    
    Args:
        coin (str): Символ монеты (например, "BTC")
        is_buy (bool): True для покупки, False для продажи
        sz (float): Размер ордера в монетах
        limit_px (float): Лимитная цена
        reduce_only (bool): Только для закрытия позиции
        tif (str): Time in force ("Gtc", "Ioc", "Alo")
    
    Returns:
        dict: Результат выполнения ордера
    """
    params = {
        "coin": coin,
        "is_buy": is_buy,
        "sz": sz,
        "limit_px": limit_px,
        "reduce_only": reduce_only,
        "tif": tif
    }
    
    logger.info(f"Размещаем лимитный ордер: {params}")
    result = execute_trade_action(None, CHAT_ID_FOR_TRADING, "limit_order", params)
    
    if result["success"]:
        logger.info(f"Ордер успешно размещен: {result['data']}")
    else:
        logger.error(f"Ошибка размещения ордера: {result['error']}")
    
    return result

def cancel_order(coin, oid):
    """
    Отменяет ордер по ID
    
    Args:
        coin (str): Символ монеты
        oid (int): ID ордера
    
    Returns:
        dict: Результат отмены ордера
    """
    params = {
        "coin": coin,
        "oid": oid
    }
    
    logger.info(f"Отменяем ордер: {params}")
    result = execute_trade_action(None, CHAT_ID_FOR_TRADING, "cancel_order", params)
    
    if result["success"]:
        logger.info(f"Ордер отменен: {result['data']}")
    else:
        logger.error(f"Ошибка отмены ордера: {result['error']}")
    
    return result

def get_current_price(coin):
    """
    Получает текущую цену монеты
    
    Args:
        coin (str): Символ монеты
    
    Returns:
        float: Текущая цена или None при ошибке
    """
    try:
        mids = get_all_mids()
        if coin in mids:
            return float(mids[coin])
        else:
            logger.error(f"Цена для {coin} не найдена")
            return None
    except Exception as e:
        logger.error(f"Ошибка получения цены для {coin}: {e}")
        return None

def monitor_and_place_orders(target_address, coin="BTC", check_interval=30):
    """
    Мониторинг и автоматическое размещение ордеров
    
    Args:
        target_address (str): Адрес для мониторинга
        coin (str): Монета для торговли
        check_interval (int): Интервал проверки в секундах
    """
    logger.info(f"Начинаем мониторинг для размещения ордеров по {coin}")
    logger.info(f"Целевой адрес: {target_address}")
    logger.info(f"Интервал проверки: {check_interval} секунд")
    
    order_count = 0
    
    try:
        while True:
            # Получаем текущую цену
            current_price = get_current_price(coin)
            if current_price is None:
                logger.warning(f"Не удалось получить цену для {coin}, пропускаем итерацию")
                time.sleep(check_interval)
                continue
            
            logger.info(f"Текущая цена {coin}: ${current_price}")
            
            # Получаем открытые ордера целевого адреса
            try:
                open_orders = get_open_orders(target_address) or []
                logger.info(f"Открытых ордеров: {len(open_orders)}")
                
                # Пример логики: размещаем ордер каждые 5 итераций
                if order_count % 5 == 0 and order_count > 0:
                    # Размещаем лимитный ордер на покупку на 1% ниже текущей цены
                    buy_price = current_price * 0.99
                    order_size = 0.001  # Небольшой размер для тестирования
                    
                    result = place_limit_order(
                        coin=coin,
                        is_buy=True,
                        sz=order_size,
                        limit_px=buy_price,
                        reduce_only=False,
                        tif="Gtc"
                    )
                    
                    if result["success"]:
                        logger.info(f"Ордер #{order_count} размещен успешно")
                
                order_count += 1
                
            except Exception as e:
                logger.error(f"Ошибка при получении открытых ордеров пользователя: {e}")
            
            # Ждем до следующей проверки
            time.sleep(check_interval)
            
    except KeyboardInterrupt:
        logger.info("Мониторинг остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка в мониторинге: {e}")

def main():
    """
    Главная функция для демонстрации размещения ордеров
    """
    logger.info("=== Демонстрация размещения лимитных ордеров через polling ===")
    logger.info("")
    
    # Пример адреса (замените на реальный)
    target_address = "0x563b377a956c80d77a7c613a9343699ad6123911"
    
    logger.info("Выберите действие:")
    logger.info("1. Разместить лимитный ордер")
    logger.info("2. Отменить ордер")
    logger.info("3. Получить текущую цену")
    logger.info("4. Запустить автоматический мониторинг")
    logger.info("5. Выход")
    
    while True:
        try:
            choice = input("\nВведите номер действия: ").strip()
            
            if choice == "1":
                coin = input("Введите символ монеты (например, BTC): ").strip().upper()
                is_buy = input("Покупка? (y/n): ").strip().lower() == 'y'
                sz = float(input("Введите размер ордера: "))
                limit_px = float(input("Введите лимитную цену: "))
                
                result = place_limit_order(coin, is_buy, sz, limit_px)
                logger.info(f"Результат: {result}")
                
            elif choice == "2":
                coin = input("Введите символ монеты: ").strip().upper()
                oid = int(input("Введите ID ордера: "))
                
                result = cancel_order(coin, oid)
                logger.info(f"Результат: {result}")
                
            elif choice == "3":
                coin = input("Введите символ монеты: ").strip().upper()
                price = get_current_price(coin)
                if price:
                    logger.info(f"Текущая цена {coin}: ${price}")
                else:
                    logger.warning("Не удалось получить цену")
                    
            elif choice == "4":
                coin = input("Введите символ монеты для мониторинга (по умолчанию BTC): ").strip().upper() or "BTC"
                interval = int(input("Введите интервал проверки в секундах (по умолчанию 30): ") or "30")
                
                monitor_and_place_orders(target_address, coin, interval)
                
            elif choice == "5":
                logger.info("Выход...")
                break
                
            else:
                logger.warning("Неверный выбор. Попробуйте снова.")
                
        except KeyboardInterrupt:
            logger.info("Выход...")
            break
        except Exception as e:
            logger.error(f"Ошибка: {e}")

if __name__ == "__main__":
    main()