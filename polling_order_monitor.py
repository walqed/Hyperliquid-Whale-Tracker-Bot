#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Мониторинг ордеров через REST API запросы (polling)
Альтернатива WebSocket мониторингу
"""

import time
import logging
import requests
from typing import Dict, List, Any, Optional
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL

logger = logging.getLogger(__name__)

class OrderPollingMonitor:
    def __init__(self, wallet_address: str, check_interval: int = 5):
        self.wallet_address = wallet_address.lower()
        self.check_interval = check_interval
        self.info = Info(MAINNET_API_URL, skip_ws=True)
        self.last_orders = {}
        self.last_fills = {}
        self.notifications = []
        self.running = False
        
    def get_user_orders(self) -> List[Dict[str, Any]]:
        """Получает текущие открытые ордера пользователя"""
        try:
            orders = self.info.open_orders(self.wallet_address)
            return orders or []
        except Exception as e:
            logger.error(f"Ошибка получения ордеров: {e}")
            return []
    
    def get_user_fills(self) -> List[Dict[str, Any]]:
        """Получает последние исполнения пользователя"""
        try:
            fills = self.info.user_fills(self.wallet_address)
            return fills or []
        except Exception as e:
            logger.error(f"Ошибка получения исполнений: {e}")
            return []
    
    def detect_new_orders(self, current_orders: List[Dict]) -> List[Dict]:
        """Определяет новые ордера"""
        new_orders = []
        current_order_ids = set()
        
        for order in current_orders:
            order_id = order.get('oid')
            if order_id:
                current_order_ids.add(order_id)
                if order_id not in self.last_orders:
                    new_orders.append({
                        'action': 'placed',
                        'order': order,
                        'timestamp': time.time()
                    })
        
        # Определяем отмененные ордера
        canceled_order_ids = set(self.last_orders.keys()) - current_order_ids
        for canceled_id in canceled_order_ids:
            new_orders.append({
                'action': 'canceled',
                'order': self.last_orders[canceled_id],
                'timestamp': time.time()
            })
        
        # Обновляем кэш ордеров
        self.last_orders = {order.get('oid'): order for order in current_orders if order.get('oid')}
        
        return new_orders
    
    def detect_new_fills(self, current_fills: List[Dict]) -> List[Dict]:
        """Определяет новые исполнения"""
        new_fills = []
        
        for fill in current_fills:
            fill_id = f"{fill.get('time', 0)}_{fill.get('oid', '')}_{fill.get('px', '')}_{fill.get('sz', '')}"
            
            if fill_id not in self.last_fills:
                new_fills.append({
                    'action': 'fill',
                    'fill': fill,
                    'timestamp': time.time()
                })
                self.last_fills[fill_id] = fill
        
        # Очищаем старые исполнения (оставляем только последние 100)
        if len(self.last_fills) > 100:
            sorted_fills = sorted(self.last_fills.items(), key=lambda x: x[1].get('time', 0))
            self.last_fills = dict(sorted_fills[-100:])
        
        return new_fills
    
    def format_order_notification(self, order_event: Dict) -> str:
        """Форматирует уведомление об ордере"""
        action = order_event['action']
        order = order_event['order']
        
        coin = order.get('coin', '?')
        side = 'LONG' if order.get('side') == 'B' else 'SHORT'
        price = float(order.get('limitPx', 0) or 0)
        size = float(order.get('sz', 0) or 0)
        size_usd = price * size if price and size else 0.0
        
        action_emoji = "📝" if action == "placed" else "🗑️"
        action_text = "Ордер размещён" if action == "placed" else "Ордер отменён"
        
        return f"{action_emoji} {action_text}: {coin} | {side} | ${size_usd:,.2f} | Цена: ${price:,.4f}"
    
    def format_fill_notification(self, fill_event: Dict) -> str:
        """Форматирует уведомление об исполнении"""
        fill = fill_event['fill']
        
        coin = fill.get('coin', '?')
        side = 'LONG' if fill.get('side') == 'B' else 'SHORT'
        price = float(fill.get('px', 0) or 0)
        size = float(fill.get('sz', 0) or 0)
        size_usd = price * size
        
        direction_emoji = "📈" if fill.get('side') == 'B' else "📉"
        if size_usd >= 1_000_000:
            size_emoji = "🐋"
        elif size_usd >= 500_000:
            size_emoji = "🦈"
        else:
            size_emoji = "🐟"
        
        return f"{size_emoji} Исполнение ордера: {coin} | {direction_emoji} {side} | ${size_usd:,.2f} | Цена: ${price:,.4f}"
    
    def start_monitoring(self, duration: int = 60) -> List[Dict]:
        """Запускает мониторинг на указанное время"""
        logger.info(f"🔔 Запуск polling мониторинга для {self.wallet_address[:10]}... на {duration} секунд")
        
        self.running = True
        start_time = time.time()
        
        # Инициализация - получаем текущее состояние
        initial_orders = self.get_user_orders()
        initial_fills = self.get_user_fills()
        
        self.last_orders = {order.get('oid'): order for order in initial_orders if order.get('oid')}
        for fill in initial_fills:
            fill_id = f"{fill.get('time', 0)}_{fill.get('oid', '')}_{fill.get('px', '')}_{fill.get('sz', '')}"
            self.last_fills[fill_id] = fill
        
        logger.info(f"✅ Инициализация: {len(self.last_orders)} ордеров, {len(self.last_fills)} исполнений")
        
        try:
            while self.running and (time.time() - start_time) < duration:
                # Проверяем ордера
                current_orders = self.get_user_orders()
                new_order_events = self.detect_new_orders(current_orders)
                
                for event in new_order_events:
                    notification = self.format_order_notification(event)
                    logger.info(notification)
                    self.notifications.append({
                        'type': 'order',
                        'message': notification,
                        'timestamp': event['timestamp'],
                        'data': event
                    })
                
                # Проверяем исполнения
                current_fills = self.get_user_fills()
                new_fill_events = self.detect_new_fills(current_fills)
                
                for event in new_fill_events:
                    notification = self.format_fill_notification(event)
                    logger.info(notification)
                    self.notifications.append({
                        'type': 'fill',
                        'message': notification,
                        'timestamp': event['timestamp'],
                        'data': event
                    })
                
                # Показываем статистику каждые 10 секунд
                elapsed = int(time.time() - start_time)
                if elapsed % 10 == 0 and elapsed > 0:
                    logger.info(f"⏱️  Polling активен: {elapsed}с | Уведомлений: {len(self.notifications)} | Ордеров: {len(self.last_orders)}")
                
                time.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            logger.info("⏹️  Получен сигнал остановки")
        finally:
            self.running = False
        
        logger.info(f"✅ Мониторинг завершен. Собрано {len(self.notifications)} уведомлений")
        return self.notifications
    
    def stop_monitoring(self):
        """Останавливает мониторинг"""
        self.running = False

def test_polling_monitor():
    """Тестирует polling мониторинг"""
    target_address = "0x162cc7c861ebd0c06b3d72319201150482518185"
    
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    monitor = OrderPollingMonitor(target_address, check_interval=3)
    
    logger.info("=" * 80)
    logger.info(f"🔍 ТЕСТ POLLING МОНИТОРИНГА: {target_address}")
    logger.info("=" * 80)
    
    notifications = monitor.start_monitoring(duration=30)  # 30 секунд
    
    logger.info("\n📬 РЕЗУЛЬТАТЫ МОНИТОРИНГА:")
    logger.info("-" * 60)
    
    if notifications:
        for i, notif in enumerate(notifications, 1):
            timestamp = time.strftime('%H:%M:%S', time.localtime(notif['timestamp']))
            logger.info(f"{i:2d}. [{timestamp}] {notif['message']}")
    else:
        logger.info("📭 Новых событий не обнаружено")
    
    logger.info("\n" + "=" * 80)
    logger.info("✅ ТЕСТ ЗАВЕРШЕН")
    logger.info("=" * 80)

if __name__ == "__main__":
    test_polling_monitor()