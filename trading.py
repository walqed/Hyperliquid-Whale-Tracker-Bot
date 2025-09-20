# trading.py
import json
import logging
import os
import sqlite3
import math
import time
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL
from eth_account import Account

import config
from hyperliquid_api import get_all_mids, get_user_positions_with_sdk

logger = logging.getLogger(__name__)


def _load_fernet() -> Fernet:
    """Loads Fernet from env ENCRYPTION_KEY or encryption.key file; generates if missing.
    This avoids hardcoding secrets in the repo and ensures the bot can work out of the box.
    """
    key_env = os.getenv("ENCRYPTION_KEY")
    if key_env:
        try:
            key_bytes = key_env.encode()
            # Validate by constructing Fernet
            return Fernet(key_bytes)
        except Exception as e:
            logger.error(f"Invalid ENCRYPTION_KEY in env: {e}")
            # fallthrough to file-based

    key_path = os.path.join(os.path.dirname(__file__), "encryption.key")
    try:
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                return Fernet(f.read())
    except Exception as e:
        logger.warning(f"Failed to read encryption.key: {e}")

    # Generate a new key and persist it
    try:
        new_key = Fernet.generate_key()
        with open(key_path, "wb") as f:
            f.write(new_key)
        logger.info("Generated new encryption key and saved to encryption.key")
        return Fernet(new_key)
    except Exception as e:
        logger.error(f"Failed to generate/save encryption key: {e}")
        # As a last resort, create in-memory key (won't persist across restarts)
        return Fernet(Fernet.generate_key())


fernet = _load_fernet()

# Instrument precision cache and quantization helpers
_META_CACHE: dict | None = None
_META_CACHE_TS: float = 0.0
_META_TTL: float = 300.0


def _get_precisions(coin: str) -> tuple[int, int]:
    try:
        global _META_CACHE, _META_CACHE_TS
        now = time.time()
        if (_META_CACHE is None) or (now - _META_CACHE_TS > _META_TTL):
            info = Info(MAINNET_API_URL, skip_ws=True)
            _META_CACHE = info.meta() or {}
            _META_CACHE_TS = now
        for asset in (_META_CACHE.get("universe") or []):
            if (asset.get("name") or "").upper() == (coin or "").upper():
                px_dec = int(asset.get("pxDecimals") or asset.get("pxDecimal") or 2)
                sz_dec = int(asset.get("szDecimals") or asset.get("szDecimal") or 4)
                return px_dec, sz_dec
    except Exception as e:
        logger.warning(f"Не удалось получить метаданные точности для {coin}: {e}")
    return 2, 4


def _quantize_size(sz: float, sz_decimals: int) -> float:
    step = 10 ** (-sz_decimals)
    if step <= 0:
        return max(0.0, sz)
    q = math.floor(sz / step) * step
    q = max(step, q)
    return round(q, sz_decimals)


def _quantize_price(px: float, px_decimals: int) -> float:
    step = 10 ** (-px_decimals)
    if step <= 0:
        return px
    q = round(px / step) * step
    return round(q, px_decimals)


def execute_trade_action(user_id: Optional[int], chat_id: int, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Executes a trading action for the user.

    Lookup order for encrypted key:
      1) user_trade_keys by user_id (new, allows group trading by individual users)
      2) user_trade_wallets by chat_id (legacy, for backward compatibility)

    Actions:
      - market_open: params={coin:str, is_buy:bool, sz_usd:float}
      - market_close: params={coin:str}
      - update_leverage: params={coin:str, leverage:int}
      - limit_order: params={coin:str, is_buy:bool, sz:float, limit_px:float, reduce_only?:bool, tif?:str}
      - cancel_order: params={coin:str, oid:int}
    """
    # 1) Load encrypted key from DB
    try:
        with sqlite3.connect(config.DB_FILE, check_same_thread=False) as conn:
            cursor = conn.cursor()
            result = None
            if user_id is not None:
                cursor.execute(
                    "SELECT encrypted_key FROM user_trade_keys WHERE user_id = ?",
                    (user_id,),
                )
                result = cursor.fetchone()
            if not result:
                cursor.execute(
                    "SELECT encrypted_key FROM user_trade_wallets WHERE chat_id = ?",
                    (chat_id,),
                )
                result = cursor.fetchone()
            if not result:
                return {
                    "success": False,
                    "error": "Торговый ключ не найден. Настройте его командой /set_key (в ЛС с ботом)."
                }
        private_key = fernet.decrypt(result[0]).decode()
    except Exception as e:
        logger.error(
            f"Ошибка при получении/расшифровке ключа для user_id {user_id} / chat_id {chat_id}: {e}",
            exc_info=True,
        )
        return {"success": False, "error": "Не удалось обработать ваш сохраненный ключ."}

    # 2) Instantiate exchange with proper wallet object
    try:
        wallet = Account.from_key(private_key)
        exchange = Exchange(wallet=wallet, base_url=MAINNET_API_URL)
        address = wallet.address
    except Exception as e:
        logger.error(f"Ошибка инициализации Exchange: {e}")
        return {"success": False, "error": "Ошибка инициализации биржи."}

    # 3) Execute requested action
    try:
        if action == "market_open":
            coin = params["coin"]
            is_buy = params["is_buy"]
            sz_usd = float(params["sz_usd"])

            # Pre-flight: проверяем доступные средства аккаунта
            try:
                state = get_user_positions_with_sdk(address) or {}
                margin = state.get('marginSummary', {}) or {}
                cross = state.get('crossMarginSummary', {}) or {}
                account_value = float(margin.get('accountValue', cross.get('totalRawUsd', 0)) or 0)
            except Exception:
                account_value = 0.0
            if account_value <= 0:
                return {"success": False, "error": f"Недостаточно средств (Account Value = ${account_value:,.2f})."}

            # Convert USD size to coin amount using current mid price
            mids = get_all_mids()
            if not mids or coin not in mids:
                return {"success": False, "error": f"Не удалось получить цену для {coin}."}
            try:
                px = float(mids[coin])
            except Exception:
                return {"success": False, "error": f"Некорректная цена для {coin}."}
            if px <= 0:
                return {"success": False, "error": f"Некорректная цена для {coin}."}

            sz_coins = sz_usd / px
            px_dec, sz_dec = _get_precisions(coin)
            sz_coins_q = _quantize_size(sz_coins, sz_dec)
            if sz_coins_q <= 0:
                return {"success": False, "error": "Слишком маленький размер ордера."}

            status = exchange.market_open(coin, is_buy, sz_coins_q, px=None, slippage=0.01)
            # Robust status parsing
            if isinstance(status, dict):
                if status.get("error"):
                    return {"success": False, "error": status["error"]}
                resp = status.get("response") or status.get("data") or status
                if isinstance(resp, dict):
                    st = (resp.get("status") or resp.get("result") or "").lower()
                    if st and st != "ok":
                        return {"success": False, "error": f"Сервер отклонил маркет-ордер: {resp}"}
            # If we got here, request was accepted by API
            return {"success": True, "data": f"Маркет-заявка на ${sz_usd} по {coin} отправлена. Проверяю биржу на исполнение..."}

        elif action == "limit_order":
            coin = params["coin"]
            is_buy = params["is_buy"]
            sz = float(params["sz"])  # размер в монетах
            limit_px = float(params["limit_px"])  # лимитная цена
            reduce_only = params.get("reduce_only", False)
            tif = params.get("tif", "Gtc")  # time in force: Gtc, Ioc, Alo
            # Normalize TIF value to expected capitalization
            if isinstance(tif, str):
                tif = tif.strip().lower()
                if tif in ("gtc", "ioc", "alo"):
                    tif = tif.capitalize()
                else:
                    tif = "Gtc"

            # Pre-flight: если это не reduce-only, проверим, что на аккаунте есть средства
            if not reduce_only:
                try:
                    state = get_user_positions_with_sdk(address) or {}
                    margin = state.get('marginSummary', {}) or {}
                    cross = state.get('crossMarginSummary', {}) or {}
                    account_value = float(margin.get('accountValue', cross.get('totalRawUsd', 0)) or 0)
                except Exception:
                    account_value = 0.0
                if account_value <= 0:
                    return {"success": False, "error": f"Недостаточно средств (Account Value = ${account_value:,.2f})."}
            else:
                # Для reduce-only: проверим наличие позиции в нужном направлении
                try:
                    state = get_user_positions_with_sdk(address) or {}
                    positions = state.get('assetPositions', []) or []
                    
                    # Найдем позицию по монете
                    target_position = None
                    for pos in positions:
                        p = pos.get('position', {}) or {}
                        if p.get('coin') == coin:
                            target_position = p
                            break
                    
                    if not target_position:
                        return {"success": False, "error": f"Нет позиции по {coin} для reduce-only ордера."}
                    
                    # Проверим, что направление reduce-only соответствует позиции
                    position_size = float(target_position.get('szi', 0) or 0)
                    
                    if position_size == 0:
                        return {"success": False, "error": f"Нет позиции по {coin} для reduce-only ордера."}
                    
                    # position_size > 0 = LONG позиция, нужен reduce-only SELL
                    # position_size < 0 = SHORT позиция, нужен reduce-only BUY
                    if position_size > 0 and is_buy:
                        return {"success": False, "error": f"У вас LONG позиция по {coin}. Для reduce-only используйте SELL, а не BUY."}
                    elif position_size < 0 and not is_buy:
                        return {"success": False, "error": f"У вас SHORT позиция по {coin}. Для reduce-only используйте BUY, а не SELL."}
                        
                except Exception as e:
                    logger.warning(f"Ошибка при проверке позиции для reduce-only: {e}")
                    # Не блокируем ордер при ошибке API, пусть биржа сама разберется

            px_dec, sz_dec = _get_precisions(coin)
            sz_q = _quantize_size(sz, sz_dec)
            px_q = _quantize_price(limit_px, px_dec)
            if sz_q <= 0:
                return {"success": False, "error": "Слишком маленький размер ордера."}

            status = exchange.order(
                coin,
                is_buy,
                sz_q,
                px_q,
                order_type={"limit": {"tif": tif}},
                reduce_only=reduce_only,
            )
            if isinstance(status, dict):
                if status.get("error"):
                    return {"success": False, "error": status["error"]}
                resp = status.get("response") or status.get("data") or status
                if isinstance(resp, dict):
                    st = (resp.get("status") or resp.get("result") or "").lower()
                    if st and st != "ok":
                        return {"success": False, "error": f"Сервер отклонил лимитный ордер: {resp}"}

            side_text = "покупки" if is_buy else "продажи"
            return {"success": True, "data": f"Лимитная заявка на {side_text} {sz_q} {coin} по цене ${px_q} отправлена."}

        elif action == "cancel_order":
            coin = params["coin"]
            oid = int(params["oid"])  # order id

            status = exchange.cancel(coin, oid)
            if isinstance(status, dict) and status.get("error"):
                return {"success": False, "error": status["error"]}
            return {"success": True, "data": f"Ордер #{oid} по {coin} отменен."}

        elif action == "market_close":
            coin = params["coin"]
            status = exchange.market_close(coin)
            if isinstance(status, dict) and status.get("error"):
                return {"success": False, "error": status["error"]}
            return {"success": True, "data": f"Позиция по {coin} закрыта."}

        elif action == "update_leverage":
            coin = params["coin"]
            leverage = int(params["leverage"])
            status = exchange.update_leverage(leverage, coin, is_cross=True)
            if isinstance(status, dict) and status.get("error"):
                return {"success": False, "error": status["error"]}
            return {"success": True, "data": f"Плечо для {coin} установлено на x{leverage}."}

        else:
            return {"success": False, "error": "Неизвестное действие."}

    except Exception as e:
        logger.error(f"Ошибка при выполнениии '{action}': {e}", exc_info=True)
        error_message = str(e)
        # Try to unwrap JSON error from server
        if "message" in error_message:
            try:
                # e.g., "Error: {\"message\": \"...\"}"
                _, payload = error_message.split(":", 1)
                error_details = json.loads(payload)
                error_message = error_details.get("message", "Неизвестная ошибка от сервера.")
            except Exception as e:
                logger.debug(f"Не удалось распарсить сообщение об ошибке сервера: {e}")
        return {"success": False, "error": error_message}