# monitoring.py
import logging
import time
import asyncio
import threading
import signal
import sys
from collections import deque
from typing import Dict, Any, Set, Optional, Union
from hyperliquid.websocket_manager import WebsocketManager
from math import floor
import database
import random
from hyperliquid_api import convert_token_number_to_name

logger = logging.getLogger(__name__)

class SimpleRateLimiter:
    """Простой rate limiter для контроля частоты запросов"""
    def __init__(self, max_requests_per_minute=60):
        self.max_requests_per_minute = max_requests_per_minute
        self.current_minute = self._get_current_minute()
        self.current_requests = 0
    
    def _get_current_minute(self) -> int:
        return floor(time.time() / 60) * 60
    
    def _reset_if_new_minute(self) -> None:
        current = self._get_current_minute()
        if current > self.current_minute:
            self.current_minute = current
            self.current_requests = 0
    
    def can_make_request(self) -> bool:
        self._reset_if_new_minute()
        return self.current_requests < self.max_requests_per_minute
    
    def add_request(self) -> None:
        self._reset_if_new_minute()
        self.current_requests += 1

# Очередь и множество для обмена данными с основным потоком
notification_queue = None  # Будет создана при инициализации
subscribed_wallets = set()
connection_status = {"connected": False, "last_heartbeat": 0}
rate_limiter = SimpleRateLimiter(max_requests_per_minute=50)

def init_notification_queue():
    """Инициализация очереди уведомлений с новым event loop"""
    global notification_queue
    try:
        notification_queue = asyncio.Queue()
        logger.info("✅ Очередь уведомлений инициализирована")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации очереди уведомлений: {e}")
        return False

# Глобальные переменные для управления
ws_manager = None
monitor_thread = None
shutdown_event = threading.Event()

def handle_shutdown(signum=None, frame=None):
    """Обработчик сигналов завершения"""
    if shutdown_event.is_set():
        sys.exit(0)
    
    logger.info("🛑 Получен сигнал завершения, выполняется graceful shutdown...")
    shutdown_event.set()
    
    # Закрываем WebSocket соединение
    global ws_manager
    if ws_manager:
        try:
            ws_manager.close()
            logger.info("✅ WebSocket соединение закрыто")
        except Exception as e:
            logger.error(f"❌ Ошибка при закрытии WebSocket: {e}")
    
    # Ждем завершения потока мониторинга
    global monitor_thread
    if monitor_thread and monitor_thread.is_alive():
        monitor_thread.join(timeout=5)
        logger.info("✅ Поток мониторинга завершен")
    
    logger.info("🏁 Graceful shutdown завершен")
    sys.exit(0)

# Устанавливаем обработчики сигналов
signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

# Тайминги для контроля соединения
HEARTBEAT_WARN_SEC = 120         # предупредить если долго нет событий
HEARTBEAT_RECONNECT_SEC = 180    # перезагрузить WS, если тишина слишком долго
SUBSCRIBE_PAUSE_SEC = 0.1        # пауза между (un)subscribe, чтобы не спамить
CHECK_INTERVAL_SEC = 10          # период основного цикла (уменьшен для лучшей отзывчивости)
# Параметры backoff для реконнекта
RECONNECT_BASE_DELAY_SEC = 1     # уменьшен базовый delay
RECONNECT_MAX_DELAY_SEC = 30     # уменьшен максимальный delay
MAX_RECONNECT_ATTEMPTS = 10      # максимальное количество попыток переподключения


def on_user_event(event: Dict[str, Any]):
    """Callback-функция, которая обрабатывает события от WebSocket."""
    try:
        # Обновляем статус подключения
        connection_status["last_heartbeat"] = time.time()
        connection_status["connected"] = True
        
        # Добавляем отладочный лог для всех входящих событий
        logger.info(f"🔍 Получено WebSocket событие: {event}")
        
        if not event:
            logger.debug("Получено пустое событие")
            return
        
        # Обрабатываем как канал 'userEvents', так и канал 'user'
        if event.get("channel") in ["userEvents", "user"]:
            evt = event.get("data", {})
            evt_type = evt.get("type")

            # --- Новый формат: массивы 'fills' и 'orderUpdates' внутри userEvents ---
            # Обработка fills как массива
            if isinstance(evt.get("fills"), list) and evt.get("fills"):
                for fill in evt["fills"]:
                    try:
                        wallet = (fill.get("user") or evt.get("user") or "").lower()
                        coin = convert_token_number_to_name(fill.get("coin"))
                        price = float(fill.get("px", 0) or 0)
                        size = float(fill.get("sz", 0) or 0)
                        size_usd = price * size
                        side = "LONG" if fill.get("side") == "B" else "SHORT"

                        direction_emoji = "📈" if fill.get("side") == "B" else "📉"
                        if size_usd >= 1_000_000:
                            size_emoji = "🐋"
                        elif size_usd >= 500_000:
                            size_emoji = "🦈"
                        else:
                            size_emoji = "🐟"

                        logger.info(f"{size_emoji} Новая сделка(userEvents.fills): {wallet[:10]}... | {coin} | {direction_emoji} {side} | ${size_usd:,.2f}")

                        if not wallet:
                            continue

                        tracking_users = database.get_users_tracking_wallet(wallet)
                        for chat_id in tracking_users:
                            user_threshold = database.get_user_threshold(chat_id)
                            
                            # Для ордеров применяем тот же порог по нотации, если она доступна
                            if size_usd >= user_threshold:
                                try:
                                    if notification_queue is not None:
                                        notification_queue.put_nowait({
                                            "chat_id": chat_id,
                                            "kind": "fill",
                                            # Нормализованный payload под format_fill_message
                                            "payload": {"data": {"type": "fill", "data": fill}},
                                            "timestamp": time.time(),
                                        })
                                    else:
                                        logger.warning("⚠️ Очередь уведомлений не инициализирована")
                                except asyncio.QueueFull:
                                    logger.warning(f"⚠️ Очередь уведомлений переполнена для пользователя {chat_id}")
                            else:
                                logger.debug(f"🔕 Сделка ${size_usd:,.2f} ниже порога ${user_threshold:,.0f} для пользователя {chat_id}")
                    except Exception as inner_e:
                        logger.error(f"Ошибка обработки fill в массиве: {inner_e}", exc_info=True)

            # Обработка orderUpdates как массива
            if isinstance(evt.get("orderUpdates"), list) and evt.get("orderUpdates"):
                for update in evt["orderUpdates"]:
                    try:
                        order_wrapper = update or {}
                        action = None
                        if "placed" in order_wrapper:
                            action = "placed"
                            details = order_wrapper.get("placed", {}) or {}
                        elif "canceled" in order_wrapper:
                            action = "canceled"
                            details = order_wrapper.get("canceled", {}) or {}
                        elif "cancelled" in order_wrapper:
                            action = "canceled"
                            details = order_wrapper.get("cancelled", {}) or {}
                        else:
                            details = {}

                        if not details:
                            logger.debug("Получен элемент orderUpdates без полей placed/canceled — пропускаю")
                            continue

                        wallet = (details.get("user") or order_wrapper.get("user") or evt.get("user") or "").lower()
                        coin = convert_token_number_to_name(details.get("coin") or order_wrapper.get("coin") or evt.get("coin") or "?")
                        price = float(details.get("px", 0) or 0)
                        size = float(details.get("sz", 0) or 0)
                        size_usd = price * size if price and size else 0.0
                        side = "LONG" if (details.get("side") or "").upper() in ("B", "BUY", "LONG") else "SHORT"

                        if action == "placed":
                            act_emoji = "📝"
                            act_text = "Ордер размещён"
                        else:
                            act_emoji = "🗑️"
                            act_text = "Ордер отменён"

                        logger.info(f"{act_emoji} {act_text} (userEvents.orderUpdates): {wallet[:10]}... | {coin} | {side} | ${size_usd:,.2f}")

                        if not wallet:
                            continue

                        tracking_users = database.get_users_tracking_wallet(wallet)
                        for chat_id in tracking_users:
                            order_threshold = database.get_user_order_threshold(chat_id)
                            if size_usd >= order_threshold:
                                try:
                                    if notification_queue is not None:
                                        notification_queue.put_nowait({
                                            "chat_id": chat_id,
                                            "kind": "order",
                                            "order_action": action,
                                            # Нормализованный payload под format_order_message
                                            "payload": {"data": {"type": "orderUpdate", "data": {action if action != "cancelled" else "canceled": details}}},
                                            "timestamp": time.time(),
                                        })
                                    else:
                                        logger.warning("⚠️ Очередь уведомлений не инициализирована")
                                except asyncio.QueueFull:
                                    logger.warning(f"⚠️ Очередь уведомлений переполнена для пользователя {chat_id}")
                            else:
                                logger.debug(f"🔕 Ордер ${size_usd:,.2f} ниже порога ${order_threshold:,.0f} для пользователя {chat_id}")
                    except Exception as inner_e:
                        logger.error(f"Ошибка обработки orderUpdate в массиве: {inner_e}", exc_info=True)

            # --- Старый формат: типизированные события ---
            # 1) Исполнения (fills)
            if evt_type == "fill":
                fill = evt["data"]
                wallet = (fill.get("user") or "").lower()
                coin = convert_token_number_to_name(fill["coin"])
                price = float(fill.get("px", 0) or 0)
                size = float(fill.get("sz", 0) or 0)
                size_usd = price * size
                side = "LONG" if fill.get("side") == "B" else "SHORT"

                # Определяем эмодзи для логов
                direction_emoji = "📈" if fill.get("side") == "B" else "📉"
                if size_usd >= 1_000_000:
                    size_emoji = "🐋"
                elif size_usd >= 500_000:
                    size_emoji = "🦈"
                else:
                    size_emoji = "🐟"

                logger.info(f"{size_emoji} Новая сделка: {wallet[:10]}... | {coin} | {direction_emoji} {side} | ${size_usd:,.2f}")

                # Получаем всех пользователей, отслеживающих этот кошелек
                tracking_users = database.get_users_tracking_wallet(wallet)
                
                for chat_id in tracking_users:
                    user_threshold = database.get_user_threshold(chat_id)
                    
                    if size_usd >= user_threshold:
                        logger.info(f"🔔 Отправляем уведомление пользователю {chat_id} (порог: ${user_threshold:,.0f})")
                        try:
                            if notification_queue is not None:
                                notification_queue.put_nowait({
                                    "chat_id": chat_id,
                                    "kind": "fill",
                                    "payload": event,
                                    "timestamp": time.time(),
                                })
                            else:
                                logger.warning("⚠️ Очередь уведомлений не инициализирована")
                        except asyncio.QueueFull:
                            logger.warning(f"⚠️ Очередь уведомлений переполнена для пользователя {chat_id}")
                    else:
                        logger.debug(f"🔕 Сделка ${size_usd:,.2f} ниже порога ${user_threshold:,.0f} для пользователя {chat_id}")

            # 2) Обновления ордеров (размещение / отмена)
            elif evt_type in ("orderUpdate", "order_update", "order"):  # поддержка разных вариантов
                order_wrapper = evt.get("data", {}) or {}
                # Определяем действие
                action = None
                if "placed" in order_wrapper:
                    action = "placed"
                    details = order_wrapper.get("placed", {}) or {}
                elif "canceled" in order_wrapper:
                    action = "canceled"
                    details = order_wrapper.get("canceled", {}) or {}
                elif "cancelled" in order_wrapper:  # на всякий случай британская орфография
                    action = "canceled"
                    details = order_wrapper.get("cancelled", {}) or {}
                else:
                    details = {}

                if not details:
                    logger.debug("Получен orderUpdate без полей placed/canceled — пропускаю")
                    return

                wallet = (details.get("user") or order_wrapper.get("user") or "").lower()
                coin = convert_token_number_to_name(details.get("coin") or order_wrapper.get("coin") or "?")
                price = float(details.get("px", 0) or 0)
                size = float(details.get("sz", 0) or 0)
                size_usd = price * size if price and size else 0.0
                side = "LONG" if details.get("side") == "B" else "SHORT"

                # Эмодзи и текст для логов
                if action == "placed":
                    act_emoji = "📝"
                    act_text = "Ордер размещён"
                else:
                    act_emoji = "🗑️"
                    act_text = "Ордер отменён"

                logger.info(f"{act_emoji} {act_text}: {wallet[:10]}... | {coin} | {side} | ${size_usd:,.2f}")

                # Получаем подписчиков кошелька
                if not wallet:
                    logger.debug("OrderUpdate без поля user — пропуск")
                    return

                tracking_users = database.get_users_tracking_wallet(wallet)
                for chat_id in tracking_users:
                    order_threshold = database.get_user_order_threshold(chat_id)

                    # Для ордеров применяем отдельный порог order_threshold
                    if size_usd >= order_threshold:
                         try:
                             if notification_queue is not None:
                                 notification_queue.put_nowait({
                                     "chat_id": chat_id,
                                     "kind": "order",
                                     "order_action": action,
                                     "payload": event,
                                     "timestamp": time.time(),
                                 })
                             else:
                                 logger.warning("⚠️ Очередь уведомлений не инициализирована")
                         except asyncio.QueueFull:
                             logger.warning(f"⚠️ Очередь уведомлений переполнена для пользователя {chat_id}")
                    else:
                        logger.debug(f"🔕 Ордер ${size_usd:,.2f} ниже порога ${order_threshold:,.0f} для пользователя {chat_id}")
                     
    except Exception as e:
        logger.error(f"❌ Ошибка в on_user_event: {e}", exc_info=True)


def on_connection_status_change(connected: bool):
    """Callback для отслеживания статуса подключения WebSocket."""
    connection_status["connected"] = connected
    if connected:
        logger.info("🟢 WebSocket подключение установлено")
    else:
        logger.warning("🔴 WebSocket подключение потеряно")


def _safe_unsubscribe(ws: WebsocketManager, wallet: str):
    """Пытается отписаться от кошелька, игнорируя нефатальные ошибки.
    Если метод unsubscribe отсутствует в SDK, просто логируем и продолжаем.
    """
    try:
        if hasattr(ws, "unsubscribe"):
            ws.unsubscribe({"type": "userEvents", "user": wallet})
            logger.info(f"🔕 Отписка от кошелька: {wallet[:10]}...")
        else:
            logger.warning("⚠️ Метод unsubscribe недоступен в WebsocketManager — пропускаю прямую отписку")
        time.sleep(SUBSCRIBE_PAUSE_SEC)
    except Exception as e:
        logger.error(f"❌ Ошибка отписки от {wallet[:10]}...: {e}")


def _safe_subscribe(ws: WebsocketManager, wallet: str):
    """Пытается подписаться на кошелек, игнорируя нефатальные ошибки."""
    try:
        # Подписываемся на userEvents для получения orderUpdates
        ws.subscribe({"type": "userEvents", "user": wallet}, on_user_event)
        logger.info(f"🔔 Подписка на userEvents для кошелька: {wallet[:10]}...")
        time.sleep(SUBSCRIBE_PAUSE_SEC)
        
        # Подписываемся на userFills для получения fills
        ws.subscribe({"type": "userFills", "user": wallet}, on_user_event)
        logger.info(f"🔔 Подписка на userFills для кошелька: {wallet[:10]}...")
        time.sleep(SUBSCRIBE_PAUSE_SEC)
    except Exception as e:
        error_msg = str(e)
        if "multiple times" in error_msg or "already subscribed" in error_msg.lower():
            logger.debug(f"📝 Кошелек {wallet[:10]}... уже отслеживается")
        else:
            logger.error(f"❌ Ошибка подписки на {wallet[:10]}...: {e}")


def _recreate_ws(ws: Optional[WebsocketManager]) -> Optional[WebsocketManager]:
    """Переcоздает WebSocketManager, корректно закрывая старый, если возможно."""
    try:
        if ws is not None:
            # Принудительно закрываем соединение
            try:
                if hasattr(ws, "close"):
                    ws.close()
                    logger.info("🔌 Старый WebSocket закрыт")
                # Дополнительно останавливаем поток если есть
                if hasattr(ws, "stop_event") and ws.stop_event:
                    ws.stop_event.set()
                # Ждем немного для полного закрытия
                time.sleep(1)
            except Exception as e:
                logger.warning(f"⚠️ Не удалось корректно закрыть WS: {e}")
    except Exception as e:
        logger.debug(f"Грейсфул закрытие WS-обертки завершилось с ошибкой: {e}")

    try:
        from hyperliquid.utils.constants import MAINNET_API_URL
        ws_new = WebsocketManager(base_url=MAINNET_API_URL)
        
        # Устанавливаем callback для отслеживания статуса соединения
        if hasattr(ws_new, 'set_connection_callback'):
            ws_new.set_connection_callback(on_connection_status_change)
        
        # Запускаем WebSocket соединение
        ws_new.start()
        logger.info("✅ WebSocket менеджер создан и запущен")
        
        # Ждем установления соединения с улучшенной проверкой
        max_wait = 15  # увеличен до 15 секунд
        wait_time = 0
        connection_established = False
        while not connection_established and wait_time < max_wait:
            # Проверяем несколько условий для установления соединения
            ws_ready = getattr(ws_new, 'ws_ready', False)
            is_alive = getattr(ws_new, 'is_alive', lambda: False)()
            
            if ws_ready and is_alive:
                connection_established = True
                logger.info("🟢 WebSocket подключение установлено")
                connection_status["connected"] = True
                connection_status["last_heartbeat"] = time.time()
                break
            
            time.sleep(0.5)
            wait_time += 0.5
            
            # Логируем прогресс каждые 3 секунды
            if int(wait_time) % 3 == 0 and wait_time > 0:
                logger.debug(f"⏳ Ожидание подключения... {wait_time:.1f}с (ws_ready: {ws_ready}, is_alive: {is_alive})")
        
        if not connection_established:
            logger.warning(f"🔴 WebSocket не смог подключиться за {max_wait}с")
            connection_status["connected"] = False
            # Не возвращаем None сразу, даем шанс работать частично
            logger.info("⚠️ Возвращаем WebSocket менеджер для попытки работы в частичном режиме")
        
        return ws_new
    except Exception as e:
        logger.error(f"❌ Ошибка создания WebSocket менеджера: {e}")
        return None


def monitor_worker():
    """Фоновый поток, управляющий подписками WebSocket."""
    global subscribed_wallets, ws_manager
    logger.info("🚀 Запуск воркера мониторинга WebSocket...")
    
    # Инициализация WebSocket менеджера
    ws = _recreate_ws(None)
    if ws is None:
        logger.error("❌ Не удалось создать WebSocket менеджер")
        return
    
    ws_manager = ws  # Сохраняем ссылку для graceful shutdown
    retry_count = 0
    max_retries = 5
    
    while not shutdown_event.is_set():
        try:
            # Получаем текущий список кошельков из БД
            wallets_in_db = database.get_all_unique_wallets()
            logger.debug(f"📊 Кошельков в БД: {len(wallets_in_db)}, подписок активно: {len(subscribed_wallets)}")
            
            # Подписываемся на новые кошельки с rate limiting
            new_wallets = wallets_in_db - subscribed_wallets
            if new_wallets:
                logger.info(f"➕ Новые кошельки для отслеживания: {len(new_wallets)}")
                for wallet in new_wallets:
                    if wallet not in subscribed_wallets:  # Дополнительная проверка
                        if rate_limiter.can_make_request():
                            _safe_subscribe(ws, wallet)
                            subscribed_wallets.add(wallet)
                            rate_limiter.add_request()
                        else:
                            logger.debug(f"⏳ Rate limit достигнут, пропускаем подписку на {wallet[:10]}...")
                            break
            
            # Отписываемся от удаленных кошельков
            removed_wallets = subscribed_wallets - wallets_in_db
            if removed_wallets:
                logger.info(f"➖ Удаляем подписки: {len(removed_wallets)} кошельков")
                for wallet in removed_wallets:
                    _safe_unsubscribe(ws, wallet)
                    subscribed_wallets.discard(wallet)
            
            # Проверяем статус подключения
            current_time = time.time()
            idle_sec = current_time - (connection_status["last_heartbeat"] or 0)
            ws_connected = ws and ws.ws_ready and ws.is_alive()

            if ws_connected and connection_status["connected"]:
                if idle_sec > HEARTBEAT_WARN_SEC:  # долго нет сообщений
                    logger.warning("⚠️ Долго нет сообщений от WebSocket, возможны проблемы с подключением")
                # Если тишина слишком долгая — пробуем пересоздать WS и ресабскрайбиться
                if idle_sec > HEARTBEAT_RECONNECT_SEC:
                    logger.warning("🔄 WS долго молчит — пересоздаю соединение и ресабскрайблюсь")
                    ws_new = _recreate_ws(ws)
                    if ws_new is not None:
                        ws = ws_new
                        ws_manager = ws_new  # Обновляем ссылку
                        # Очищаем список подписок и ресабскрайбимся
                        subscribed_wallets.clear()
                        for wallet in wallets_in_db:
                            if rate_limiter.can_make_request():
                                _safe_subscribe(ws, wallet)
                                subscribed_wallets.add(wallet)
                                rate_limiter.add_request()
                            else:
                                logger.debug(f"⏳ Rate limit достигнут при ресабскрайбе на {wallet[:10]}...")
                                break
                        retry_count = 0
                    else:
                        retry_count += 1
                        if retry_count >= MAX_RECONNECT_ATTEMPTS:
                            logger.error(f"❌ Достигнуто максимальное количество попыток переподключения ({MAX_RECONNECT_ATTEMPTS}). Сброс счетчика.")
                            retry_count = 0
                            time.sleep(RECONNECT_MAX_DELAY_SEC)  # длительная пауза перед сбросом
                        else:
                            # экспоненциальный backoff с джиттером
                            delay = min(RECONNECT_BASE_DELAY_SEC * (2 ** min(retry_count, 5)), RECONNECT_MAX_DELAY_SEC)
                            jitter = random.uniform(0, 0.3 * delay)
                            sleep_time = delay + jitter
                            logger.info(f"⏰ Пауза перед реконнектом после тишины: {sleep_time:.1f}с (попытка #{retry_count}/{MAX_RECONNECT_ATTEMPTS})")
                            time.sleep(sleep_time)
            else:
                # фиксируем инкремент до логирования, чтобы номер попытки в логе соответствовал фактическому
                retry_count += 1
                if retry_count >= MAX_RECONNECT_ATTEMPTS:
                    logger.error(f"❌ Достигнуто максимальное количество попыток переподключения ({MAX_RECONNECT_ATTEMPTS}). Сброс счетчика.")
                    retry_count = 0
                    time.sleep(RECONNECT_MAX_DELAY_SEC)  # длительная пауза перед сбросом
                    continue
                    
                if not ws_connected:
                    logger.warning(f"🔴 WebSocket не подключен (ws_ready: {ws.ws_ready if ws else 'None'}, is_alive: {ws.is_alive() if ws else 'None'}), попытка #{retry_count}/{MAX_RECONNECT_ATTEMPTS}")
                else:
                    logger.warning(f"🔴 WebSocket подключен, но статус connection_status неверный, попытка #{retry_count}/{MAX_RECONNECT_ATTEMPTS}")
                    
                ws_new = _recreate_ws(ws)
                if ws_new is not None:
                    ws = ws_new
                    ws_manager = ws_new  # Обновляем ссылку
                    # Очищаем список подписок и ресабскрайбимся
                    subscribed_wallets.clear()
                    for wallet in wallets_in_db:
                        if rate_limiter.can_make_request():
                            _safe_subscribe(ws, wallet)
                            subscribed_wallets.add(wallet)
                            rate_limiter.add_request()
                        else:
                            logger.debug(f"⏳ Rate limit достигнут при ресабскрайбе на {wallet[:10]}...")
                            break
                    retry_count = 0
                else:
                    # экспоненциальный backoff с джиттером
                    delay = min(RECONNECT_BASE_DELAY_SEC * (2 ** min(retry_count, 5)), RECONNECT_MAX_DELAY_SEC)
                    jitter = random.uniform(0, 0.3 * delay)
                    sleep_time = delay + jitter
                    logger.info(f"⏰ Пауза перед реконнектом: {sleep_time:.1f}с (попытка #{retry_count})")
                    time.sleep(sleep_time)
                
                if retry_count >= max_retries:
                    logger.error(f"💥 Превышено максимальное количество попыток переподключения ({max_retries})")
                    logger.error("🔄 Перезапустите бота для восстановления мониторинга")
                    break
            
            # Статистика в логах каждые 5 минут
            if int(current_time) % 300 == 0:  # каждые 5 минут
                connected_status = "🟢 Подключен" if connection_status['connected'] else "🔴 Отключен"
                logger.info(f"📊 Статистика мониторинга:")
                logger.info(f"   WebSocket: {connected_status}")
                logger.info(f"   🔔 Активных подписок: {len(subscribed_wallets)}")
                queue_size = notification_queue.qsize() if notification_queue is not None else 0
                logger.info(f"   📬 Очередь уведомлений: {queue_size} сообщений")
            
            time.sleep(CHECK_INTERVAL_SEC)  # Проверяем изменения каждые N секунд
            
        except KeyboardInterrupt:
            logger.info("⏹️ Получен сигнал остановки мониторинга")
            break
        except Exception as e:
            logger.error(f"❌ Ошибка в цикле monitor_worker: {e}", exc_info=True)
            retry_count += 1
            
            if retry_count >= max_retries:
                logger.error("💥 Слишком много ошибок в monitor_worker, остановка")
                break
            
            sleep_time = min(30 * retry_count, 300)  # Увеличиваем паузу, но не больше 5 минут
            logger.info(f"⏰ Пауза {sleep_time} секунд перед повторной попыткой...")
            time.sleep(sleep_time)
    
    logger.error("⛔ Мониторинг WebSocket остановлен")


def start_monitoring():
    """Запуск мониторинга в отдельном потоке."""
    global monitor_thread
    
    # Проверяем, не установлен ли флаг завершения
    if shutdown_event.is_set():
        logger.warning("⚠️ Система завершается, мониторинг не будет запущен")
        return False
    
    if monitor_thread is None or not monitor_thread.is_alive():
        try:
            monitor_thread = threading.Thread(target=monitor_worker, daemon=True, name="WebSocketMonitor")
            monitor_thread.start()
            logger.info("✅ Поток мониторинга WebSocket запущен")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка запуска потока мониторинга: {e}")
            return False
    else:
        logger.info("ℹ️ Поток мониторинга уже активен")
        return True


def stop_monitoring():
    """Остановка мониторинга."""
    global monitor_thread, ws_manager
    
    logger.info("🛑 Остановка мониторинга WebSocket...")
    shutdown_event.set()
    
    # Закрываем WebSocket соединение
    if ws_manager:
        try:
            ws_manager.close()
            logger.info("✅ WebSocket соединение закрыто")
        except Exception as e:
            logger.error(f"❌ Ошибка при закрытии WebSocket: {e}")
    
    # Ждем завершения потока
    if monitor_thread and monitor_thread.is_alive():
        monitor_thread.join(timeout=10)
        if monitor_thread.is_alive():
            logger.warning("⚠️ Поток мониторинга не завершился за 10 секунд")
        else:
            logger.info("✅ Поток мониторинга завершен")
    
    return True

def get_monitoring_stats():
    """Возвращает статистику мониторинга."""
    return {
        "connected": connection_status["connected"],
        "subscribed_wallets": len(subscribed_wallets),
        "queue_size": notification_queue.qsize() if notification_queue is not None else 0,
        "last_heartbeat": connection_status["last_heartbeat"],
        "rate_limiter_requests": rate_limiter.current_requests,
        "rate_limiter_max": rate_limiter.max_requests_per_minute,
        "shutdown_requested": shutdown_event.is_set(),
        "monitor_thread_alive": monitor_thread.is_alive() if monitor_thread else False
    }