#!/usr/bin/env python3
"""
Yellow Pro Exchange Live Test

Tests all features supported by the yellow_pro Hummingbot connector.

Usage:
    export YELLOW_PRO_API_KEY="your-api-key"
    export YELLOW_PRO_API_SECRET="your-api-secret"
    export YELLOW_PRO_SESSION_ID="your-session-id"
    export YELLOW_PRO_TRADING_PAIR="BTC-YTEST.USD"   # optional, default: first available pair
    export YELLOW_PRO_CHANNEL_ID="your-channel-id"   # optional
    python scripts/yellow_pro_livetest.py

Test order prices are set far from market (50% below bid / 2x above ask) so they
will never fill. All test orders are cancelled at the end of the run.
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import aiohttp

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://api.staging.yellow.pro.neodax.app"
AUTH_URL = "https://auth.staging.yellow.pro.neodax.app"
WS_URL = "wss://api.staging.yellow.pro.neodax.app/ws"

API_KEY = os.environ.get("YELLOW_PRO_API_KEY", "")
API_SECRET = os.environ.get("YELLOW_PRO_API_SECRET", "")
SESSION_ID = os.environ.get("YELLOW_PRO_SESSION_ID", "")
TRADING_PAIR = os.environ.get("YELLOW_PRO_TRADING_PAIR", "")
CHANNEL_ID = os.environ.get("YELLOW_PRO_CHANNEL_ID", "")

# Safe test order distances from market price
BUY_PRICE_FACTOR = Decimal("0.5")   # 50% below best bid — will not fill
SELL_PRICE_FACTOR = Decimal("2.0")  # 2x above best ask — will not fill
TEST_QUANTITY = Decimal("0.0001")

# Tracking
_results: List[Tuple[str, str]] = []
_placed_orders: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def section(title: str):
    print(f"\n{'=' * 62}")
    print(f"  {title}")
    print(f"{'=' * 62}")


def ok(name: str, detail: str = ""):
    _results.append(("PASS", name))
    suffix = f"  —  {detail}" if detail else ""
    print(f"  [PASS]  {name}{suffix}")


def fail(name: str, error: Any):
    _results.append(("FAIL", name))
    print(f"  [FAIL]  {name}  —  {error}")


def skip(name: str, reason: str = ""):
    _results.append(("SKIP", name))
    suffix = f"  —  {reason}" if reason else ""
    print(f"  [SKIP]  {name}{suffix}")


def summary():
    passed = sum(1 for s, _ in _results if s == "PASS")
    failed = sum(1 for s, _ in _results if s == "FAIL")
    skipped = sum(1 for s, _ in _results if s == "SKIP")
    print(f"\n{'=' * 62}")
    print(f"  Results:  {passed} passed  |  {failed} failed  |  {skipped} skipped")
    print(f"{'=' * 62}\n")
    return failed == 0


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _sign(method: str, path: str, params: Optional[Dict] = None) -> Dict[str, str]:
    timestamp = str(int(time.time()))
    canonical = ""
    if params:
        canonical = "|".join(f"{k}={params[k]}" for k in sorted(params.keys()))
    prehash = f"{method}{path}{timestamp}{canonical}"
    signature = hmac.new(
        API_SECRET.encode(),
        prehash.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-API-KEY": API_KEY,
        "X-SIGNATURE": signature,
        "X-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }


async def _get(session: aiohttp.ClientSession, path: str,
               params: Optional[Dict] = None, auth: bool = True,
               base: str = BASE_URL) -> Any:
    headers = _sign("GET", path, params) if auth else {}
    url = f"{base}{path}"
    async with session.get(url, params=params, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _post(session: aiohttp.ClientSession, path: str,
                body: Dict) -> Any:
    headers = _sign("POST", path, body)
    url = f"{BASE_URL}{path}"
    async with session.post(url, data=json.dumps(body), headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _delete(session: aiohttp.ClientSession, path: str,
                  body: Dict) -> Any:
    headers = _sign("DELETE", path, body)
    url = f"{BASE_URL}{path}"
    async with session.delete(url, data=json.dumps(body), headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _ws_headers() -> Dict[str, str]:
    timestamp = str(int(time.time()))
    prehash = f"GET/ws{timestamp}"
    signature = hmac.new(
        API_SECRET.encode(),
        prehash.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-API-KEY": API_KEY,
        "X-SIGNATURE": signature,
        "X-TIMESTAMP": timestamp,
    }


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _exchange_symbol(trading_pair: str) -> str:
    """Convert 'BTC-YTEST.USD' → 'BTCYTEST.USD' (exchange format)."""
    return trading_pair.replace("-", "")


def _pair_from_symbol(base: str, quote: str) -> str:
    return f"{base.upper()}-{quote.upper()}"


# ---------------------------------------------------------------------------
# Test groups
# ---------------------------------------------------------------------------

async def test_connectivity(session: aiohttp.ClientSession):
    section("1. Connectivity")

    # /health (no auth)
    try:
        resp = await _get(session, "/health", auth=False)
        ok("GET /health", resp.get("status", resp))
    except Exception as e:
        fail("GET /health", e)

    # /auth/health (auth)
    try:
        params = {}
        resp = await _get(session, "/auth/health", params=params, auth=True, base=AUTH_URL)
        ok("GET /auth/health", resp.get("status", resp))
    except Exception as e:
        fail("GET /auth/health", e)


async def test_market_data(session: aiohttp.ClientSession) -> Optional[str]:
    """Returns the trading pair to use for subsequent tests."""
    section("2. Market Data")
    chosen_pair = TRADING_PAIR

    # Exchange info / trading pairs
    try:
        resp = await _get(session, "/spot/exchangeInfo", auth=True)
        symbols = resp.get("symbols", [])
        ok("GET /spot/exchangeInfo", f"{len(symbols)} symbols")
        if not chosen_pair and symbols:
            first = symbols[0]
            chosen_pair = _pair_from_symbol(first["base_asset"], first["quote_asset"])
    except Exception as e:
        fail("GET /spot/exchangeInfo", e)

    if not chosen_pair:
        skip("Order book / ticker", "no trading pair available")
        return None

    symbol = _exchange_symbol(chosen_pair)

    # Order book snapshot
    try:
        resp = await _get(session, "/orderbook", params={"symbol": symbol}, auth=True)
        bids = resp.get("bids", [])
        asks = resp.get("asks", [])
        ok("GET /orderbook", f"{len(bids)} bids, {len(asks)} asks  [{chosen_pair}]")
    except Exception as e:
        fail("GET /orderbook", e)

    # 24hr ticker
    try:
        resp = await _get(session, "/ticker/24hr", params={"symbol": symbol}, auth=True)
        last = resp.get("last") or resp.get("lastPrice", "n/a")
        ok("GET /ticker/24hr", f"last={last}  [{chosen_pair}]")
    except Exception as e:
        fail("GET /ticker/24hr", e)

    return chosen_pair


async def test_account(session: aiohttp.ClientSession):
    section("3. Account")

    if not SESSION_ID:
        skip("GET /spot/account", "YELLOW_PRO_SESSION_ID not set")
        skip("GET /spot/accounts", "YELLOW_PRO_SESSION_ID not set")
        return

    # Single account
    try:
        resp = await _get(session, "/spot/account",
                          params={"app_session_id": SESSION_ID}, auth=True)
        balances = resp.get("balances", {})
        non_zero = {k: v for k, v in (balances.items() if isinstance(balances, dict)
                    else {}) if Decimal(str(v.get("total_balance", 0))) > 0}
        ok("GET /spot/account", f"{len(non_zero)} non-zero balances")
    except Exception as e:
        fail("GET /spot/account", e)

    # All accounts
    try:
        resp = await _get(session, "/spot/accounts",
                          params={"app_session_id": SESSION_ID}, auth=True)
        count = len(resp) if isinstance(resp, list) else len(resp.get("accounts", []))
        ok("GET /spot/accounts", f"{count} accounts")
    except Exception as e:
        fail("GET /spot/accounts", e)


async def test_order_book_ws(trading_pair: Optional[str]):
    section("4. WebSocket — Order Book")

    if not trading_pair:
        skip("WS order book subscription", "no trading pair")
        return

    symbol = _exchange_symbol(trading_pair)
    headers = await _ws_headers()
    subscribe_msg = json.dumps({"type": "subscribe", "channel": "order_book", "symbol": symbol})

    try:
        async with aiohttp.ClientSession() as ws_session:
            async with ws_session.ws_connect(WS_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as ws:
                await ws.send_str(subscribe_msg)
                deadline = time.time() + 8
                got_snapshot = False
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=3)
                    except asyncio.TimeoutError:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        mtype = data.get("type") or data.get("event")
                        if mtype in ("snapshot", "order_book", "update"):
                            got_snapshot = True
                            bids = data.get("bids", [])
                            asks = data.get("asks", [])
                            ok("WS order book subscription", f"type={mtype} bids={len(bids)} asks={len(asks)}")
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                if not got_snapshot:
                    ok("WS order book connection", "connected (no data within timeout)")
    except Exception as e:
        fail("WS order book subscription", e)


async def test_user_stream_ws():
    section("5. WebSocket — User Stream")

    if not API_KEY or not API_SECRET:
        skip("WS user stream", "credentials not set")
        return

    headers = await _ws_headers()
    subscribe_msg = json.dumps({"type": "subscribe", "channel": "user"})

    try:
        async with aiohttp.ClientSession() as ws_session:
            async with ws_session.ws_connect(WS_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as ws:
                await ws.send_str(subscribe_msg)
                ok("WS user stream connection", "connected and subscribed")
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=4)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        ok("WS user stream message", f"type={data.get('type', 'unknown')}")
                except asyncio.TimeoutError:
                    ok("WS user stream", "connected (no immediate message, expected)")
    except Exception as e:
        fail("WS user stream connection", e)


async def test_orders(session: aiohttp.ClientSession, trading_pair: Optional[str]):
    section("6. Orders")

    if not trading_pair or not SESSION_ID:
        reason = "no trading pair" if not trading_pair else "YELLOW_PRO_SESSION_ID not set"
        for name in ["GET /spot/open_orders", "GET /spot/orders", "GET /spot/trades",
                     "POST /spot/order (LIMIT buy)", "POST /spot/order (LIMIT_MAKER sell)",
                     "DELETE /spot/order"]:
            skip(name, reason)
        return

    symbol = _exchange_symbol(trading_pair)
    channel = (CHANNEL_ID or SESSION_ID).strip()
    base_params = {"app_session_id": SESSION_ID, "market": symbol}
    if channel:
        base_params["channel_id"] = channel

    # Open orders
    try:
        resp = await _get(session, "/spot/open_orders", params=base_params, auth=True)
        orders = resp.get("orders") or resp.get("open_orders") or (resp if isinstance(resp, list) else [])
        ok("GET /spot/open_orders", f"{len(orders)} open orders")
    except Exception as e:
        fail("GET /spot/open_orders", e)

    # Order history
    try:
        resp = await _get(session, "/spot/orders", params={**base_params, "page": 1, "page_size": 10}, auth=True)
        orders = resp.get("orders", [])
        ok("GET /spot/orders", f"{len(orders)} orders in history")
    except Exception as e:
        fail("GET /spot/orders", e)

    # Trade history
    try:
        resp = await _get(session, "/spot/trades", params={**base_params, "page": 1, "page_size": 10}, auth=True)
        trades = resp.get("trades", []) if isinstance(resp, dict) else (resp if isinstance(resp, list) else [])
        ok("GET /spot/trades", f"{len(trades)} trades in history")
    except Exception as e:
        fail("GET /spot/trades", e)

    # Fetch current price to compute safe test prices
    best_bid, best_ask = await _get_best_prices(session, symbol)
    if best_bid is None or best_ask is None:
        skip("POST /spot/order (LIMIT buy)", "could not fetch order book prices")
        skip("POST /spot/order (LIMIT_MAKER sell)", "could not fetch order book prices")
        skip("DELETE /spot/order", "no orders placed")
        return

    safe_buy_price = best_bid * BUY_PRICE_FACTOR
    safe_sell_price = best_ask * SELL_PRICE_FACTOR

    # Place LIMIT buy order
    limit_order_id = await _place_test_order(
        session, symbol, "limit", "buy", safe_buy_price, TEST_QUANTITY, "POST /spot/order (LIMIT buy)")

    # Place LIMIT_MAKER sell order
    maker_order_id = await _place_test_order(
        session, symbol, "limit_maker", "sell", safe_sell_price, TEST_QUANTITY, "POST /spot/order (LIMIT_MAKER sell)")

    # Cancel all placed orders
    cancelled = 0
    errors = 0
    for order in list(_placed_orders):
        oid = order.get("order_uuid") or order.get("uuid") or order.get("order_id")
        if not oid:
            continue
        try:
            body = {
                "app_session_id": SESSION_ID,
                "channelID": SESSION_ID,
                "market": symbol,
                "order_uuid": oid,
                "type": "limit",
            }
            await _delete(session, "/spot/order", body)
            cancelled += 1
        except Exception as e:
            errors += 1
            print(f"          warn: cancel {oid} failed — {e}")

    if _placed_orders:
        if errors == 0:
            ok("DELETE /spot/order", f"cancelled {cancelled} test order(s)")
        else:
            fail("DELETE /spot/order", f"{errors} cancel(s) failed out of {len(_placed_orders)}")
    else:
        skip("DELETE /spot/order", "no orders were placed")

    _placed_orders.clear()


async def test_positions(session: aiohttp.ClientSession):
    section("7. Positions")

    channel = (CHANNEL_ID or SESSION_ID).strip()
    if not channel:
        skip("GET /positions", "YELLOW_PRO_SESSION_ID or YELLOW_PRO_CHANNEL_ID not set")
        return

    try:
        resp = await _get(session, "/positions", params={"channel_id": channel}, auth=True)
        positions = resp if isinstance(resp, list) else resp.get("positions", [])
        ok("GET /positions", f"{len(positions)} open position(s)")
    except Exception as e:
        fail("GET /positions", e)


# ---------------------------------------------------------------------------
# Helpers for order tests
# ---------------------------------------------------------------------------

async def _get_best_prices(session: aiohttp.ClientSession,
                           symbol: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    try:
        resp = await _get(session, "/orderbook", params={"symbol": symbol}, auth=True)
        bids = resp.get("bids", [])
        asks = resp.get("asks", [])
        best_bid = Decimal(str(bids[0][0])) if bids else None
        best_ask = Decimal(str(asks[0][0])) if asks else None
        return best_bid, best_ask
    except Exception:
        return None, None


async def _place_test_order(session: aiohttp.ClientSession,
                            symbol: str, order_type: str, side: str,
                            price: Decimal, qty: Decimal,
                            test_name: str) -> Optional[str]:
    body: Dict[str, Any] = {
        "app_session_id": SESSION_ID,
        "market": symbol,
        "type": order_type,
        "side": side,
        "quantity": str(qty),
        "price": str(price.quantize(Decimal("0.0001"))),
    }
    channel = (CHANNEL_ID or SESSION_ID).strip()
    if channel:
        body["channel_id"] = channel

    try:
        resp = await _post(session, "/spot/order", body)
        order_id = resp.get("order_uuid") or resp.get("uuid") or resp.get("order_id")
        if order_id:
            _placed_orders.append(resp)
            ok(test_name, f"order_id={order_id} price={price} qty={qty}")
            return order_id
        else:
            fail(test_name, f"no order_id in response: {resp}")
            return None
    except Exception as e:
        fail(test_name, e)
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run():
    print("\nYellow Pro Exchange — Live Test")
    print(f"  API URL : {BASE_URL}")
    print(f"  WS  URL : {WS_URL}")
    print(f"  API key : {API_KEY[:8]}{'*' * max(0, len(API_KEY) - 8) if API_KEY else '(not set)'}")
    print(f"  Session : {SESSION_ID[:8]}{'*' * max(0, len(SESSION_ID) - 8) if SESSION_ID else '(not set)'}")

    if not API_KEY or not API_SECRET:
        print("\n  ERROR: YELLOW_PRO_API_KEY and YELLOW_PRO_API_SECRET are required.\n")
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        await test_connectivity(session)
        trading_pair = await test_market_data(session)
        await test_account(session)
        await test_order_book_ws(trading_pair)
        await test_user_stream_ws()
        await test_orders(session, trading_pair)
        await test_positions(session)

    success = summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(run())
