# 📋 Руководство по управлению ордерами

Этот документ описывает функциональность управления ордерами на Hyperliquid через:
1. **Telegram бот** - удобное управление через команды в мессенджере
2. **Polling систему** - программное управление через REST API

Оба способа позволяют размещать лимитные ордера, отменять их и получать текущие цены без использования WebSocket соединений.

## 🤖 Управление через Telegram (Рекомендуется)

### Настройка
1. Запустите бота: `python main.py`
2. Найдите бота в Telegram и отправьте `/start`
3. **В личных сообщениях** установите торговый ключ: `/set_key 0xВАШ_ПРИВАТНЫЙ_КЛЮЧ`

### Доступные команды

#### Лимитные ордера
```
/limit_order <монета> <buy/sell> <сумма_USD> <цена>

# Примеры:
/limit_order ETH buy 100 3500     # Купить ETH на $100 по цене $3500
/limit_order BTC sell 500 65000   # Продать BTC на $500 по цене $65000
```

#### Отмена ордеров
```
/cancel_order <монета> <order_id>

# Пример:
/cancel_order ETH 12345           # Отменить ордер #12345 для ETH
```

#### Рыночные ордера
```
/buy <монета> <сумма_USD>         # Открыть лонг позицию
/sell <монета> <сумма_USD>        # Открыть шорт позицию
/close <монета>                   # Закрыть позицию
```

#### Получение цены
```
/price <монета>                   # Текущая цена монеты

# Примеры:
/price ETH                        # Цена Ethereum
/price BTC                        # Цена Bitcoin
```

#### Управление рычагом
```
/leverage <монета> <значение>     # Установить плечо

# Пример:
/leverage ETH 10                  # Установить плечо x10 для ETH
```

### Демонстрация команд
Запустите демонстрационный скрипт для просмотра всех команд:
```bash
python telegram_order_demo.py
```

## 💻 Программное управление (Polling)

### Обзор

Теперь проект поддерживает размещение ордеров через polling API вместо WebSocket. Это более надежный подход для автоматической торговли.

## Доступные функции

### 1. Лимитные ордера

```python
from trading import execute_trade_action

# Размещение лимитного ордера на покупку
result = execute_trade_action("limit_order", {
    "coin": "BTC",
    "is_buy": True,
    "sz": 0.001,  # размер в монетах
    "limit_px": 45000.0,  # лимитная цена
    "reduce_only": False,
    "tif": "Gtc"  # Good Till Cancelled
})

print(result)
# {'success': True, 'data': 'Лимитный ордер на покупки 0.001 BTC по цене $45000.0 размещен.'}
```

### 2. Отмена ордеров

```python
# Отмена ордера по ID
result = execute_trade_action("cancel_order", {
    "coin": "BTC",
    "oid": 12345  # ID ордера
})

print(result)
# {'success': True, 'data': 'Ордер #12345 по BTC отменен.'}
```

### 3. Маркет ордера (уже существовали)

```python
# Маркет ордер на покупку
result = execute_trade_action("market_open", {
    "coin": "BTC",
    "is_buy": True,
    "sz_usd": 100.0  # размер в USD
})
```

## Параметры лимитных ордеров

### Обязательные параметры:
- `coin` (str): Символ монеты (например, "BTC", "ETH")
- `is_buy` (bool): True для покупки, False для продажи
- `sz` (float): Размер ордера в монетах (не в USD!)
- `limit_px` (float): Лимитная цена

### Опциональные параметры:
- `reduce_only` (bool): Только для закрытия позиции (по умолчанию False)
- `tif` (str): Time in Force (по умолчанию "Gtc")
  - `"Gtc"` - Good Till Cancelled (действует до отмены)
  - `"Ioc"` - Immediate or Cancel (исполнить немедленно или отменить)
  - `"Alo"` - Add Liquidity Only (только добавление ликвидности)

## Использование готового скрипта

В проекте есть готовый скрипт `polling_order_placement.py` для демонстрации:

```bash
python polling_order_placement.py
```

Скрипт предоставляет интерактивное меню:
1. Разместить лимитный ордер
2. Отменить ордер
3. Получить текущую цену
4. Запустить автоматический мониторинг
5. Выход

## Автоматический мониторинг и размещение ордеров

```python
from polling_order_placement import monitor_and_place_orders

# Запуск автоматического мониторинга
monitor_and_place_orders(
    target_address="0x563b377a956c80d77a7c613a9343699ad6123911",
    coin="BTC",
    check_interval=30  # проверка каждые 30 секунд
)
```

## Получение текущих цен

```python
from polling_order_placement import get_current_price

price = get_current_price("BTC")
if price:
    print(f"Текущая цена BTC: ${price}")
```

## Интеграция с существующим мониторингом

Вы можете интегрировать размещение ордеров с существующим polling мониторингом:

```python
from final_wallet_test import start_polling_monitoring
from polling_order_placement import place_limit_order, get_current_price

# В функции обработки событий мониторинга
def on_large_trade_detected(coin, size, price):
    """Обработчик крупных сделок"""
    if size > 1000000:  # если сделка больше $1M
        # Размещаем ордер на покупку на 0.5% ниже цены
        limit_price = price * 0.995
        
        result = place_limit_order(
            coin=coin,
            is_buy=True,
            sz=0.01,  # небольшой размер
            limit_px=limit_price
        )
        
        if result["success"]:
            print(f"Автоматически размещен ордер: {result['data']}")
```

## Обработка ошибок

Все функции возвращают словарь с результатом:

```python
result = execute_trade_action("limit_order", params)

if result["success"]:
    print(f"Успех: {result['data']}")
else:
    print(f"Ошибка: {result['error']}")
```

## Логирование

Все операции с ордерами логируются в файл `polling_orders.log`:

```
2024-01-15 10:30:15 - INFO - Размещаем лимитный ордер: {'coin': 'BTC', 'is_buy': True, 'sz': 0.001, 'limit_px': 45000.0}
2024-01-15 10:30:16 - INFO - Ордер успешно размещен: Лимитный ордер на покупки 0.001 BTC по цене $45000.0 размещен.
```

## Безопасность

⚠️ **Важно**: 
- Всегда тестируйте с небольшими суммами
- Проверяйте параметры ордеров перед размещением
- Используйте `reduce_only=True` для безопасного закрытия позиций
- Мониторьте логи для отслеживания всех операций

## Примеры стратегий

### 1. Сетка ордеров (Grid Trading)

```python
def create_grid_orders(coin, center_price, grid_size=5, step_percent=0.5):
    """Создает сетку лимитных ордеров"""
    for i in range(grid_size):
        # Ордера на покупку ниже центральной цены
        buy_price = center_price * (1 - (i + 1) * step_percent / 100)
        place_limit_order(coin, True, 0.001, buy_price)
        
        # Ордера на продажу выше центральной цены
        sell_price = center_price * (1 + (i + 1) * step_percent / 100)
        place_limit_order(coin, False, 0.001, sell_price)
```

### 2. Следование за трендом

```python
def trend_following_orders(coin, current_price, trend_direction):
    """Размещает ордера в направлении тренда"""
    if trend_direction == "up":
        # Покупаем на откате
        buy_price = current_price * 0.98
        place_limit_order(coin, True, 0.01, buy_price)
    elif trend_direction == "down":
        # Продаем на отскоке
        sell_price = current_price * 1.02
        place_limit_order(coin, False, 0.01, sell_price)
```

## Заключение

Теперь проект поддерживает полноценную торговлю через polling API. Это обеспечивает:
- Надежное размещение ордеров
- Простую интеграцию с мониторингом
- Гибкие стратегии автоматической торговли
- Полное логирование всех операций

Используйте `polling_order_placement.py` для тестирования и разработки собственных торговых стратегий.