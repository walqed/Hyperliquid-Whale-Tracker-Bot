# hyperliquid_api.py
import logging
import requests
import time
import json
from typing import Dict, Any
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL

logger = logging.getLogger(__name__)

# Кэш для маппинга номеров токенов в названия
_token_name_cache = {}
_cache_timestamp = 0
CACHE_DURATION = 300  # 5 минут

# Добавим удобные форматтеры чисел и долларов для компактного и красивого вывода в Telegram
_DEF_THIN_NBSP = "\u202F"  # узкий неразрывный пробел


def fmt_usd(value: float | int, decimals: int = 2, show_plus: bool = False) -> str:
    try:
        v = float(value or 0)
    except Exception:
        v = 0.0
    sign = "+" if (show_plus and v > 0) else ("" if v >= 0 else "-")
    abs_str = f"{abs(v):,.{decimals}f}".replace(",", _DEF_THIN_NBSP)
    return f"{sign}${abs_str}"


def fmt_num(value: float | int, decimals: int = 4) -> str:
    try:
        v = float(value or 0)
    except Exception:
        v = 0.0
    return f"{v:,.{decimals}f}".replace(",", _DEF_THIN_NBSP)


def fmt_num_signed(value: float | int, decimals: int = 2) -> str:
    try:
        v = float(value or 0)
    except Exception:
        v = 0.0
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    abs_str = f"{abs(v):,.{decimals}f}".replace(",", _DEF_THIN_NBSP)
    return f"{sign}{abs_str}"


def get_token_name_mapping() -> dict[str, str]:
    """Получает маппинг номеров токенов в их названия из meta API"""
    global _token_name_cache, _cache_timestamp
    
    current_time = time.time()
    if current_time - _cache_timestamp < CACHE_DURATION and _token_name_cache:
        return _token_name_cache
    
    try:
        info = Info(MAINNET_API_URL, skip_ws=True)
        meta = info.meta()
        
        # Создаем маппинг: номер -> название
        token_mapping = {}
        for i, asset_info in enumerate(meta.get("universe", [])):
            token_name = asset_info.get("name", f"Token_{i}")
            token_mapping[str(i)] = token_name
            
        _token_name_cache = token_mapping
        _cache_timestamp = current_time
        logger.info(f"Обновлен кэш названий токенов: {len(token_mapping)} токенов")
        return token_mapping
        
    except Exception as e:
        logger.error(f"Ошибка получения маппинга токенов: {e}")
        return _token_name_cache or {}


def convert_token_number_to_name(coin: str) -> str:
    """Конвертирует номер токена в название"""
    if not coin:
        return coin
    
    # Обрабатываем токены с префиксом '@'
    if coin.startswith('@'):
        token_number = coin[1:]  # Убираем '@'
        if token_number.isdigit():
            mapping = get_token_name_mapping()
            return mapping.get(token_number, coin)
    
    # Обрабатываем обычные числовые токены
    if coin.isdigit():
        mapping = get_token_name_mapping()
        return mapping.get(coin, coin)
    
    return coin


def fmt_usd_mobile(value: float | int) -> str:
    """Компактное форматирование USD для мобильных устройств: 1.2M, 45K, 123"""
    try:
        v = float(value or 0)
    except:
        v = 0.0
    
    abs_v = abs(v)
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    
    if abs_v >= 1_000_000:
        return f"{sign}{abs_v/1_000_000:.1f}M"
    elif abs_v >= 1_000:
        return f"{sign}{abs_v/1_000:.0f}K"
    else:
        return f"{sign}{abs_v:.0f}"


def fmt_usd_compact(value: float | int, *, decimals: int = 1, show_plus: bool = False) -> str:
    """Компактный форматтер сумм с суффиксами K/M/B"""
    try:
        v = float(value)
    except Exception:
        v = 0.0
    av = abs(v)
    if av < 100_000:  # до 100k показываем как обычно, чтобы не терять точность на меньших суммах
        return fmt_usd(v, decimals=2, show_plus=show_plus)
    if av >= 1_000_000_000:
        num = av / 1_000_000_000
        suffix = 'B'
    elif av >= 1_000_000:
        num = av / 1_000_000
        suffix = 'M'
    else:  # av >= 1000
        num = av / 1_000
        suffix = 'K'
    num_str = f"{num:,.{decimals}f}"
    # Внутри компактной записи оставляем стандартную запятую-разделитель групп
    # (в отличие от длинного формата, где можем использовать узкий неразрывный пробел)
    sign = '+' if (show_plus and v > 0) else ('-' if v < 0 else '')
    return f"{sign}${num_str}{suffix}"


# Простая реализация локального rate limit + ретраи для REST-запросов
_LAST_CALL_TS: dict[str, float] = {"stats": 0.0, "info": 0.0}
_MIN_INTERVAL_SEC: dict[str, float] = {"stats": 0.3, "info": 0.2}


def _sleep_if_needed(kind: str) -> None:
    now = time.time()
    last = _LAST_CALL_TS.get(kind, 0.0)
    wait = _MIN_INTERVAL_SEC.get(kind, 0.0) - (now - last)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL_TS[kind] = time.time()


def _request_with_retries(method: str, url: str, *, kind: str, max_retries: int = 3, base_backoff: float = 0.75,
                           timeout: float = 15.0, **kwargs) -> requests.Response | None:
    for attempt in range(1, max_retries + 1):
        try:
            _sleep_if_needed(kind)
            resp: requests.Response = requests.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code in (429,) or resp.status_code >= 500:
                # сервер просит подождать или временно недоступен
                raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")
            return resp
        except requests.exceptions.RequestException as e:
            if attempt == max_retries:
                logger.error(f"Запрос {method} {url} провален после {attempt} попыток: {e}")
                return None
            backoff = base_backoff * attempt
            logger.warning(f"Ошибка запроса {method} {url} (попытка {attempt}/{max_retries}): {e}. Повтор через {backoff:.2f}s")
            time.sleep(backoff)


# --- ФУНКЦИИ ДЛЯ РАБОТЫ С REST API ---

def get_user_positions_with_sdk(address: str) -> dict | None:
    """Получает состояние пользователя через SDK.
    Возвращает словарь с полями вроде marginSummary, crossMarginSummary, assetPositions, balances и т.п.
    
    Примечание: userState через прямые REST запросы не работает (422 ошибка),
    поэтому используем только SDK, который работает корректно.
    """
    try:
        info = Info(MAINNET_API_URL, skip_ws=True)
        return info.user_state(address)
    except Exception as e:
        logger.error(f"Ошибка при получении состояния пользователя {address} через SDK: {e}")
        return None


# --- ОТКРЫТЫЕ ОРДЕРА ---

def get_open_orders(address: str) -> list[dict]:
    """Возвращает список открытых ордеров пользователя через SDK.
    Гарантирует возврат списка (в случае ошибки — пустой список).
    Ожидаемая форма элемента: {
        'coin': str, 'side': 'B'|'S', 'sz': float, 'limitPx': float, ...
    }
    """
    try:
        info = Info(MAINNET_API_URL, skip_ws=True)
        raw = info.open_orders(address)
        # Нормализация в список словарей
        if raw is None:
            return []
        if isinstance(raw, list):
            # Элементы могут быть уже dict
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            # Иногда SDK может вернуть обертку
            # Попробуем найти ключ, содержащий список ордеров
            for key in ("openOrders", "orders", "data", "result"):
                val = raw.get(key)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)]
            # Если нет явного списка, но это один ордер
            return [raw]
        # Неподдерживаемый формат
        logger.warning(f"Неожиданный формат open_orders для {address}: {type(raw).__name__}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при получении открытых ордеров {address}: {e}")
        return []


def format_open_orders(open_orders: list[dict] | None, *, page: int = 1, page_size: int = 5) -> str:
    """Форматирует список открытых ордеров постранично для Telegram.
    - Если список пуст/None — возвращает пустую строку (чтобы вызывающая сторона показала заглушку).
    - page: 1 — первая страница при вызове /orders
            2 — первая страница в режиме навигации (handlers.handle_orders_navigation)
            >=3 — соответствующие страницы в навигации
    """
    if not open_orders:
        return ""

    # Определяем смещение: в навигации первая страница ордеров имеет номер 2
    if page <= 1:
        start = 0
    elif page == 2:
        start = 0
    else:
        start = (page - 2) * page_size
    end = start + page_size

    slice_orders = open_orders[start:end]
    if not slice_orders:
        return ""

    mids = {}
    try:
        mids = get_all_mids() or {}
    except Exception:
        mids = {}

    lines: list[str] = []
    lines.append(f"<b>📑 Открытые ордера (стр {max(page,1)})</b>")
    for i, order in enumerate(slice_orders, start=1 + start):
        coin_raw = str(order.get('coin', 'N/A'))
        coin = convert_token_number_to_name(coin_raw)
        side_raw = str(order.get('side') or '').upper()
        is_buy = side_raw in ('B', 'BUY', 'LONG')
        side_text = 'LONG' if is_buy else 'SHORT'
        side_emoji = '🟢' if is_buy else '🔴'
        sz = 0.0
        try:
            sz = float(order.get('sz', 0) or 0)
        except Exception as e:
            logger.debug(f"Не удалось распарсить размер ордера sz: {e}")
        limit_px = 0.0
        try:
            limit_px = float(order.get('limitPx', order.get('px', 0)) or 0)
        except Exception as e:
            logger.debug(f"Не удалось распарсить цену ордера limitPx/px: {e}")

        # Попробуем оценить размер в USD по текущему mid
        usd_value = 0.0
        try:
            mid = float(mids.get(coin_raw) or mids.get(coin) or 0)
            if mid > 0 and sz:
                usd_value = abs(sz) * mid
        except Exception:
            usd_value = 0.0

        reduce_only_flag = order.get('reduceOnly') or order.get('reduce_only')
        reduce_text = " ⛔RO" if reduce_only_flag else ""
        tif = str(order.get('tif', '')).upper()
        tif_text = f" [{tif}]" if tif else ""

        # Строка ордера
        line = (
            f"{i}. {coin} {side_emoji} {side_text}{reduce_text}{tif_text}\n"
            f"   📏 {fmt_num(sz, 4)} | 💲 ${limit_px:,.4f}"
        )
        if usd_value > 0:
            line += f" | 💵 {fmt_usd_compact(usd_value)}"
        lines.append(line)

    return "\n".join(lines)


# --- РОЛЬ ПОЛЬЗОВАТЕЛЯ И СПОТ-БАЛАНСЫ ---

def get_user_role(address: str) -> dict | None:
    """Пытается определить роль адреса: user | subAccount | vault | agent (best-effort).
    Возвращает словарь вида {"role": str, "master": str?} или None при ошибке.
    """
    try:
        state = get_user_positions_with_sdk(address) or {}
        role = 'user'
        master = None

        # Эвристики: пробуем найти признаки сабаккаунта/хранилища и т.п.
        # В разных версиях SDK/схемы ключи могут отличаться, поэтому используем мягкие проверки.
        for key in ('subAccount', 'subaccount', 'sub_account', 'accountRole'):
            v = state.get(key)
            if isinstance(v, dict):
                role = 'subAccount'
                master = v.get('master') or v.get('parent') or v.get('masterAddress')
                break
            if isinstance(v, str) and v.lower() in ('subaccount', 'sub_account'):
                role = 'subAccount'
                break
        # vault/agent эвристики
        for key in ('vault', 'isVault', 'accountType'):
            v = state.get(key)
            if v is True or (isinstance(v, str) and v.lower() == 'vault'):
                role = 'vault'
                break
        for key in ('agent', 'isAgent'):
            v = state.get(key)
            if v is True or (isinstance(v, str) and v.lower() == 'agent'):
                role = 'agent'
                break

        result = {'role': role}
        if master:
            result['master'] = str(master)
        return result
    except Exception as e:
        logger.warning(f"Не удалось определить роль пользователя {address}: {e}")
        return {'role': 'user'}


def get_spot_balances(address: str) -> list[dict]:
    """Возвращает список спот-балансов пользователя (по возможности из user_state)."""
    try:
        state = get_user_positions_with_sdk(address) or {}
        balances = state.get('balances') or []
        if isinstance(balances, list):
            return [b for b in balances if isinstance(b, dict)]
        return []
    except Exception as e:
        logger.warning(f"Не удалось получить спот-балансы для {address}: {e}")
        return []


def format_spot_balances(balances: list[dict], *, max_items: int = 5) -> str:
    """Форматирует краткий список спот-балансов: COIN: amount (top-N)."""
    if not balances:
        return "—"
    # Оставим только значимые и отсортируем по total (если есть)
    def _total(b: dict) -> float:
        try:
            return float(b.get('total', 0) or 0)
        except Exception:
            return 0.0

    items = sorted([b for b in balances if _total(b) > 0], key=_total, reverse=True)
    if not items:
        return "—"

    shown = []
    for b in items[:max_items]:
        coin = convert_token_number_to_name(str(b.get('coin', 'N/A')))
        amt = 0.0
        try:
            amt = float(b.get('total', 0) or 0)
        except Exception as e:
            logger.debug(f"Не удалось распарсить сумму баланса: {e}")
        shown.append(f"{coin}: {fmt_num(amt, 4)}")
    if len(items) > max_items:
        shown.append(f"+{len(items) - max_items}")
    return ", ".join(shown)

def get_all_mids() -> dict[str, float]:
    """Возвращает текущие средние цены (mid prices) для всех активов в виде словаря {asset: price}.
    При любых ошибках возвращает пустой словарь, а подробности пишет в лог.
    """
    # 1) Получаем «сырые» данные от SDK
    try:
        info = Info(MAINNET_API_URL, skip_ws=True)
        raw = info.all_mids()
    except Exception as e:
        logger.error(f"Ошибка при вызове Info.all_mids(): {e}")
        return {}

    # 2) Если SDK вернул строку — пробуем распарсить как JSON
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception as e:
            logger.error(f"Info.all_mids() вернул строку, не удалось распарсить JSON: {e}; raw[:200]={raw[:200]!r}")
            return {}

    result: dict[str, float] = {}

    # 3) Поддерживаем несколько возможных форматов
    try:
        if isinstance(data, dict):
            # Вариант A: {"BTC": 12345.6, "ETH": 2345.6} или {"BTC": {"midPx": 12345.6}, ...}
            for coin, val in data.items():
                price = None
                if isinstance(val, dict):
                    price = val.get("midPx") or val.get("mid_px") or val.get("mid")
                else:
                    price = val
                if price is None:
                    continue
                try:
                    result[str(coin)] = float(price)
                except (TypeError, ValueError):
                    logger.warning(f"Не удалось привести цену для {coin}: {price}")
        elif isinstance(data, list):
            # Вариант B: список элементов разных форматов
            for item in data:
                coin = None
                price = None
                if isinstance(item, dict):
                    coin = item.get("coin") or item.get("name") or item.get("asset") or item.get("symbol")
                    price = item.get("midPx") or item.get("mid_px") or item.get("mid")
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    coin, price = item[0], item[1]
                else:
                    # Неподдерживаемый элемент
                    continue
                if coin is None or price is None:
                    continue
                try:
                    result[str(coin)] = float(price)
                except (TypeError, ValueError):
                    logger.warning(f"Не удалось привести цену для {coin}: {price}")
        else:
            logger.error(f"Неизвестный тип данных из all_mids: {type(data).__name__}")
            return {}
    except Exception as e:
        logger.error(f"Ошибка при обработке результата all_mids: {e}")
        return {}

    if not result:
        logger.warning("Список цен пуст после нормализации результата all_mids")
    else:
        logger.info(f"Получены цены для {len(result)} активов")

    return result


def get_leaderboard_data_sync() -> dict | None:
    """Получает данные лидерборда с эндпоинта статистики."""
    try:
        url = 'https://stats-data.hyperliquid.xyz/Mainnet/leaderboard'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Origin': 'https://app.hyperliquid.xyz', 
            'Referer': 'https://app.hyperliquid.xyz/'
        }
        # Используем ретраи и лимит
        resp = _request_with_retries('GET', url, kind='stats', headers=headers, timeout=15)
        if resp is None:
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при запросе к API лидерборда: {e}")
        return None


def extract_top_addresses(data: dict | None, timeframe: str = 'daily', top: int = 10) -> list[str]:
    """Возвращает список адресов из топа лидерборда по заданному периоду.
    timeframe: 'daily' | 'weekly' | 'monthly'
    top: сколько адресов вернуть
    """
    if not data:
        return []

    timeframe_config = {
        'daily': 'day',
        'weekly': 'week',
        'monthly': 'month',
    }
    period_key = timeframe_config.get(timeframe, 'day')

    leaders: list[dict] = []
    for user_data in data.get('leaderboardRows', []):
        for performance in user_data.get("windowPerformances", []):
            if performance[0] == period_key:
                pnl_value = float(performance[1].get("pnl", 0))
                leaders.append({
                    "address": user_data.get("ethAddress", "N/A"),
                    "pnl": pnl_value,
                })
                break

    leaders.sort(key=lambda x: x["pnl"], reverse=True)
    return [entry["address"] for entry in leaders[: max(0, top)]]


# --- ФУНКЦИИ-ФОРМАТТЕРЫ СООБЩЕНИЙ ---

def determine_position_direction(position_info: Dict[str, Any], default_by_value: float | None = None) -> tuple[str, str]:
    """Определяет направление позиции на основе знака размера.
    Возвращает кортеж: ("LONG"|"SHORT", emoji)
    Порядок источников: szi -> sz -> size -> side -> (фолбэк по positionValue).
    """
    # Читаем числовой размер, если доступен
    for key in ("szi", "sz", "size", "positionSize", "rawPosSize"):
        if key in position_info and position_info[key] is not None:
            try:
                size_val = float(position_info[key])
                if size_val > 0:
                    return ("LONG", "🟢")
                if size_val < 0:
                    return ("SHORT", "🔴")
            except Exception as e:
                logger.debug(f"Не удалось определить сторону по size: {e}")
    # Пытаемся по строковому полю side
    side_val = str(position_info.get("side", "")).upper()
    if side_val.startswith('B'):
        return ("LONG", "🟢")
    if side_val.startswith('S'):
        return ("SHORT", "🔴")
    # Фолбэк по value
    if default_by_value is not None:
        try:
            if float(default_by_value) >= 0:
                return ("LONG", "🟢")
            return ("SHORT", "🔴")
        except Exception as e:
            logger.debug(f"Не удалось определить сторону по default_by_value: {e}")
        return ("LONG", "🟢")


def format_fill_message(payload: Dict[str, Any]) -> str:
    """Форматирует сообщение о новой сделке (fill).
    Поддерживает два формата payload:
    1) Новый/старый event-объект: {"data": {"type": "fill", "data": { ...fill... }}}
    2) Прямо объект fill: {coin, px, sz, side, user}
    """
    try:
        fill = None
        if isinstance(payload, dict):
            # event-объект
            data = payload.get("data") if isinstance(payload.get("data"), dict) else None
            if data and str(data.get("type")).lower() == "fill" and isinstance(data.get("data"), dict):
                fill = data.get("data")
            # напрямую fill-детали
            elif {"coin", "px", "sz", "side"}.issubset(payload.keys()):
                fill = payload

        if not fill:
            return "📄 Новая сделка"

        user = (fill.get("user") or "?")
        coin = fill.get("coin") or "?"
        price = float(fill.get("px") or 0)
        size = float(fill.get("sz") or 0)
        side_raw = str(fill.get("side") or "").upper()
        is_buy = side_raw in ("B", "BUY", "LONG")
        direction = "LONG" if is_buy else "SHORT"
        direction_emoji = "🟢" if is_buy else "🔴"

        usd_value = price * size if price and size else 0.0
        # Классификация размера сделки для смодзи
        if usd_value >= 1_000_000:
            size_emoji = "🐋"
        elif usd_value >= 500_000:
            size_emoji = "🦈"
        else:
            size_emoji = "🐟"

        return (
            f"<b>{size_emoji} Сделка</b>\n\n"
            f"📝 <b>Кошелек:</b> <code>{user}</code>\n"
            f"🪙 <b>Актив:</b> {coin}\n"
            f"{direction_emoji} <b>Направление:</b> {direction}\n"
            f"📏 <b>Размер:</b> {size:,.4f} {coin}\n"
            f"💲 <b>Цена:</b> ${price:,.4f}\n"
            f"💵 <b>Номинал:</b> ${usd_value:,.2f}"
        )
    except Exception:
        return "📄 Новая сделка"


def format_order_message(event: Dict[str, Any], action: str | None = None) -> str:
    """Форматирует уведомление об ордере (размещён/отменён) из userEvents.orderUpdate.
    Поддерживает:
    - Обёртку {"data": {"type": "orderUpdate", "data": { placed|canceled|cancelled: {..details..} }}}
    - Прямо детали ордера {coin, px, sz, side, user}
    Параметр action может быть передан снаружи ("placed"/"canceled").
    """
    try:
        details: Dict[str, Any] | None = None
        resolved_action = action
        wrapper: Dict[str, Any] | None = None

        if isinstance(event, dict):
            data = event.get("data") if isinstance(event.get("data"), dict) else None
            if data and (str(data.get("type")).lower() in ("orderupdate", "order_update", "order")):
                wrapper = data.get("data") if isinstance(data.get("data"), dict) else None
            # Может быть сразу деталями
            if not wrapper and {"coin", "px", "sz"}.issubset(event.keys()):
                details = event

        if wrapper and not details:
            if "placed" in wrapper:
                resolved_action = resolved_action or "placed"
                details = wrapper.get("placed") or {}
            elif "canceled" in wrapper:
                resolved_action = resolved_action or "canceled"
                details = wrapper.get("canceled") or {}
            elif "cancelled" in wrapper:
                resolved_action = resolved_action or "canceled"
                details = wrapper.get("cancelled") or {}

        if not details:
            return "📄 Ордер обновлён"

        user = details.get('user') or "?"
        asset = details.get('coin') or details.get('asset') or 'N/A'
        side_raw = str(details.get('side') or "").upper()
        is_buy = side_raw in ("B", "BUY", "LONG")
        sz = float(details.get('sz') or details.get('szi') or 0)
        px = float(details.get('px') or details.get('price') or details.get('limitPx') or 0)

        direction = 'LONG' if is_buy else 'SHORT'
        direction_emoji = '🟢' if is_buy else '🔴'
        
        # Определяем действие и смодзи
        if resolved_action == "placed":
            action_emoji = "📝"
            action_text = "Ордер размещён"
        elif resolved_action == "canceled":
            action_emoji = "🗑️"
            action_text = "Ордер отменён"
        else:
            action_emoji = "📄"
            action_text = "Ордер обновлён"

        usd_value = px * sz if px and sz else 0.0

        return (
            f"<b>{action_emoji} {action_text}</b>\n\n"
            f"📝 <b>Кошелек:</b> <code>{user}</code>\n"
            f"🪙 <b>Актив:</b> {asset}\n"
            f"{direction_emoji} <b>Направление:</b> {direction}\n"
            f"📏 <b>Размер:</b> {sz:,.4f} {asset}\n"
            f"💲 <b>Цена:</b> ${px:,.4f}\n"
            f"💵 <b>Номинал:</b> ${usd_value:,.2f}"
        )
    except Exception:
        return "📄 Ордер обновлён"


def format_user_positions(address: str, data: dict | None, style: str = 'desktop') -> str:
    """Сообщение о позициях пользователя с поддержкой разных стилей."""
    if not data:
        return f"📝 <code>{address[:8]}...</code> ❌ Нет данных"
    
    positions = data.get('assetPositions', [])
    margin_summary = data.get('marginSummary', {}) or {}
    
    # Форматирование адреса в зависимости от стиля
    if style == 'mobile':
        addr_link = f"<code>{address}</code>"  # Полный адрес для мобильных
    else:
        addr_link = address  # Полный адрес для ПК как обычный текст
    
    lines = [f"📝 {addr_link}"]
    lines.append("")
    
    # Позиции с подробным объяснением
    if not positions:
        lines.append("🔭 Нет открытых позиций")
    else:
        lines.append("📊 <b>Открытые позиции:</b>")
        total_pnl = 0.0
        total_entry_value = 0.0
        
        for i, pos in enumerate(positions[:8]):  # Показываем до 8 позиций
            p = pos.get('position', {}) or {}
            coin = p.get('coin', 'N/A')
            pnl = float(p.get('unrealizedPnl', 0) or 0)
            size = float(p.get('szi', 0) or 0)
            entry_px = float(p.get('entryPx', 0) or 0)
            
            total_pnl += pnl
            
            # Рассчитываем стоимость входа
            entry_value = abs(size) * entry_px if entry_px > 0 else 0
            total_entry_value += entry_value
            
            # Определяем направление
            if size > 0:
                direction = "🟢 LONG"
            elif size < 0:
                direction = "🔴 SHORT"
            else:
                direction = "⚪ НЕЙТРАЛ"
            
            # Получаем информацию о плече
            leverage_info = p.get('leverage', {})
            leverage_value = leverage_info.get('value', 'N/A') if leverage_info else 'N/A'
            leverage_type = leverage_info.get('type', '') if leverage_info else ''
            
            # Форматируем плечо
            if leverage_value != 'N/A':
                leverage_text = f"⚡{leverage_value}x"
                if leverage_type:
                    leverage_text += f" ({leverage_type})"
            else:
                leverage_text = "⚡N/A"
            
            # Процент прибыли/убытка
            pnl_percent = (pnl / entry_value * 100) if entry_value > 0 else 0
            
            # Форматированная строка позиции
            lines.append(
                f"  {coin} {direction} {leverage_text}\n"
                f"  💰 Вход: {fmt_usd_compact(entry_value)} | "
                f"PnL: {fmt_usd_compact(pnl, show_plus=True)} ({pnl_percent:+.1f}%)"
            )
        
        lines.append("")
        lines.append("📈 <b>Итого по позициям:</b>")
        
        # Общий процент прибыли/убытка
        total_pnl_percent = (total_pnl / total_entry_value * 100) if total_entry_value > 0 else 0
        pnl_emoji = "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪"
        
        lines.append(f"💵 Общий вход: {fmt_usd_compact(total_entry_value)}")
        lines.append(f"{pnl_emoji} Общий PnL: {fmt_usd_compact(total_pnl, show_plus=True)} ({total_pnl_percent:+.1f}%)")
    
    # Баланс счета
    if margin_summary:
        account_value = float(margin_summary.get('accountValue', 0) or 0)
        if account_value > 0:
            lines.append("")
            lines.append(f"💼 <b>Баланс счета:</b> {fmt_usd_compact(account_value)}")
    
    lines.append("")
    lines.append("ℹ️ <i>PnL = текущая прибыль/убыток (нереализованная)</i>")
    lines.append("ℹ️ <i>Вход = стоимость позиции при открытии</i>")
    
    return "\n".join(lines)


def format_leaderboard_message(data: dict | None, timeframe: str, style: str = 'mobile') -> str:
    """Мобильно-оптимизированный лидерборд."""
    if not data:
        return "📊 Лидерборд недоступен"

    timeframe_config = {
        'daily': ('day', '📅'),
        'weekly': ('week', '📆'),
        'monthly': ('month', '🗓️'),
    }
    
    period_key, period_emoji = timeframe_config.get(timeframe, ('day', '📅'))
    
    # Собираем данные
    leaders = []
    for user_data in data.get('leaderboardRows', []):
        for performance in user_data.get("windowPerformances", []):
            if performance[0] == period_key:
                pnl_value = float(performance[1].get("pnl", 0))
                leaders.append({
                    "address": user_data.get("ethAddress", "N/A"), 
                    "pnl": pnl_value
                })
                break
    
    if not leaders:
        return f"{period_emoji} Лидерборд пуст"
    
    leaders.sort(key=lambda x: x["pnl"], reverse=True)
    
    period_text_map = {'day': 'сегодня', 'week': 'за неделю', 'month': 'за месяц'}
    header_text = period_text_map.get(period_key, 'сегодня')
    lines = [f"🏆 Топ-10 трейдеров 📅 {header_text}"]
    
    medals = ["🥇", "🥈", "🥉"]
    for i, trader in enumerate(leaders[:10]):
        rank = medals[i] if i < 3 else f"{i+1}."
        addr = trader['address']
        pnl = trader['pnl']
        
        # Форматирование адреса в зависимости от стиля
        if style == 'mobile':
            addr_block = f"<pre><code>{addr}</code></pre>"  # Блок кода для удобного копирования на мобильных
        else:
            addr_link = f"<code>{addr}</code>"  # Полный адрес для ПК в <code> для быстрого копирования
        
        # Две строки: адрес в отдельном блоке (на мобиле) или аккуратно на две строки (на десктопе)
        pnl_str = fmt_usd_compact(pnl, show_plus=True)
        if style == 'mobile':
            # Мобильный: PnL текстом + адрес отдельным блоком, чтобы Telegram не сокращал адрес и его можно было копировать по долгому тапу
            lines.append(f"{rank} 💰 {pnl_str}\n{addr_block}")
        else:
            # Desktop: оставляем старую разметку на две строки для аккуратности
            lines.append(f"{rank} {addr_link}")
            lines.append(pnl_str)
    
    return "\n".join(lines)


def format_balance_message(address: str, data: dict | None, style: str = 'mobile') -> str:
    """Баланс с поддержкой разных стилей отображения."""
    if not data:
        return f"💳 <code>{address[:8]}...</code> ❌ Нет данных"
    
    # Форматирование адреса в зависимости от стиля
    if style == 'mobile':
        addr_link = f"<code>{address}</code>"  # Полный адрес для мобильных
    else:
        addr_link = address  # Полный адрес для ПК как обычный текст
    
    lines = [f"💳 {addr_link}"]
    
    margin = data.get('marginSummary', {}) or {}
    cross = data.get('crossMarginSummary', {}) or {}
    
    # Основные показатели
    account_value = float(margin.get('accountValue', cross.get('totalRawUsd', 0)) or 0)
    free_collateral = float(cross.get('freeCollateralUsd', 0) or 0)
    margin_used = float(margin.get('totalMarginUsed', 0) or 0)
    
    if account_value > 0:
        lines.append(f"💼 Счет: {fmt_usd_mobile(account_value)}")
    if free_collateral > 0:
        lines.append(f"💵 Свободно: {fmt_usd_mobile(free_collateral)}")
    if margin_used > 0:
        lines.append(f"🧾 Маржа: {fmt_usd_mobile(margin_used)}")
    
    # PnL из позиций
    total_pnl = 0.0
    try:
        for pos in data.get('assetPositions', []):
            total_pnl += float((pos.get('position') or {}).get('unrealizedPnl', 0) or 0)
        if abs(total_pnl) > 0.01:
            pnl_emoji = "🟢" if total_pnl > 0 else "🔴"
            lines.append(f"📊 PnL: {pnl_emoji} {fmt_usd_mobile(total_pnl)}")
    except Exception as e:
        logger.debug(f"Не удалось вычислить суммарный PnL: {e}")
    
    # Спот балансы
    balances = data.get('balances', []) or []
    significant = [b for b in balances if float(b.get('total', 0) or 0) > 1]
    if significant:
        coins = ", ".join([b.get('coin', 'N/A') for b in significant[:3]])
        lines.append(f"💰 Спот: {coins}")
    
    return "\n".join(lines)