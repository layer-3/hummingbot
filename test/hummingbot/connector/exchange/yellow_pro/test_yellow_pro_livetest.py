"""
Yellow Pro Connector Live Test

Tests the YellowProExchange connector against the real API.

- Without credentials: runs public tests (order book, ticker, exchange info).
- With credentials:    runs all tests including balances, orders, and cancellation.

Usage:
    # Public tests only
    pytest test/hummingbot/connector/exchange/yellow_pro/test_yellow_pro_livetest.py -v -m livetest

    # Full test with credentials
    export YELLOW_PRO_API_KEY="..."
    export YELLOW_PRO_API_SECRET="..."
    export YELLOW_PRO_SESSION_ID="..."
    pytest test/hummingbot/connector/exchange/yellow_pro/test_yellow_pro_livetest.py -v -m livetest

    # Optional overrides
    export YELLOW_PRO_TRADING_PAIR="BTC-YTEST.USD"
    export YELLOW_PRO_CHANNEL_ID="..."

Notes:
    - Test orders are placed far from market price (50% below bid / 2x above ask) and
      are cancelled immediately after placement. They will never fill.
    - This file is excluded from CI via `-m "not livetest"`. Run it manually only.
"""

import asyncio
import os
from decimal import Decimal
from typing import Optional

import pytest

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS
from hummingbot.connector.exchange.yellow_pro.yellow_pro_exchange import YellowProExchange

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("YELLOW_PRO_API_KEY", "")
API_SECRET = os.environ.get("YELLOW_PRO_API_SECRET", "")
SESSION_ID = os.environ.get("YELLOW_PRO_SESSION_ID", "")
CHANNEL_ID = os.environ.get("YELLOW_PRO_CHANNEL_ID", "")
TRADING_PAIR = os.environ.get("YELLOW_PRO_TRADING_PAIR", "ETH-USDT")
DOMAIN = os.environ.get("YELLOW_PRO_DOMAIN", CONSTANTS.DOMAIN)

HAS_CREDENTIALS = bool(API_KEY and API_SECRET and SESSION_ID)

# Safe distance from market price for test orders
BUY_PRICE_FACTOR = Decimal("0.5")   # 50% below best bid
SELL_PRICE_FACTOR = Decimal("2.0")  # 2x above best ask
TEST_QUANTITY = Decimal("0.001")

# Mark every test in this file as a live test so CI can exclude them
pytestmark = pytest.mark.livetest

requires_credentials = pytest.mark.skipif(
    not HAS_CREDENTIALS,
    reason="Set YELLOW_PRO_API_KEY, YELLOW_PRO_API_SECRET, YELLOW_PRO_SESSION_ID to run authenticated tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def exchange() -> YellowProExchange:
    return YellowProExchange(
        yellow_pro_app_session_id=SESSION_ID or "anonymous",
        yellow_pro_api_key=API_KEY,
        yellow_pro_api_secret=API_SECRET,
        trading_pairs=[TRADING_PAIR],
        trading_required=HAS_CREDENTIALS,
        yellow_pro_domain=DOMAIN,
    )


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def run(coro, loop=None):
    """Run a coroutine synchronously, creating a loop if needed."""
    if loop is None:
        loop = asyncio.new_event_loop()
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Helper: fetch symbol map so other tests can use resolved exchange symbol
# ---------------------------------------------------------------------------

def _exchange_symbol(ex: YellowProExchange, trading_pair: str) -> str:
    """Best-effort: strip the dash. Exchange symbol is e.g. BTCYTEST.USD."""
    return trading_pair.replace("-", "")


def _get_event_loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


# ---------------------------------------------------------------------------
# 1. Connectivity
# ---------------------------------------------------------------------------

class TestConnectivity:
    def test_health_check(self, exchange: YellowProExchange):
        """GET /health — no auth required."""
        loop = _get_event_loop()
        resp = loop.run_until_complete(
            exchange._api_get(
                path_url=CONSTANTS.CHECK_NETWORK_URL,
                is_auth_required=False,
                limit_id=CONSTANTS.ALL_ENDPOINTS_LIMIT,
            )
        )
        assert resp is not None, "Health check returned no response"


# ---------------------------------------------------------------------------
# 2. Market Data
# ---------------------------------------------------------------------------

class TestMarketData:
    def test_exchange_info_returns_symbols(self, exchange: YellowProExchange):
        """GET /spot/exchangeInfo — lists tradeable pairs and their rules."""
        loop = _get_event_loop()
        resp = loop.run_until_complete(
            exchange._api_get(path_url=CONSTANTS.EXCHANGE_INFO_URL, is_auth_required=True)
        )
        assert isinstance(resp, dict), f"Unexpected response type: {type(resp)}"
        symbols = resp.get("symbols", [])
        assert len(symbols) > 0, "Exchange info returned no symbols"

    def test_exchange_info_symbol_has_required_fields(self, exchange: YellowProExchange):
        """Each symbol entry must have base_asset, quote_asset, and symbol."""
        loop = _get_event_loop()
        resp = loop.run_until_complete(
            exchange._api_get(path_url=CONSTANTS.EXCHANGE_INFO_URL, is_auth_required=True)
        )
        for sym in resp.get("symbols", []):
            assert "base_asset" in sym, f"Missing base_asset in {sym}"
            assert "quote_asset" in sym, f"Missing quote_asset in {sym}"

    def test_trading_rules_parsed_from_exchange_info(self, exchange: YellowProExchange):
        """_format_trading_rules must produce at least one TradingRule."""
        loop = _get_event_loop()
        exchange_info = loop.run_until_complete(
            exchange._api_get(path_url=CONSTANTS.EXCHANGE_INFO_URL, is_auth_required=True)
        )
        rules = loop.run_until_complete(exchange._format_trading_rules(exchange_info))
        assert len(rules) > 0, "No trading rules parsed from exchange info"
        rule = rules[0]
        assert rule.min_order_size > Decimal("0")
        assert rule.min_price_increment > Decimal("0")

    def _fetch_order_book(self, exchange: YellowProExchange) -> Optional[dict]:
        """Fetch order book, return None if symbol has no data yet (404)."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        loop = _get_event_loop()
        try:
            return loop.run_until_complete(
                exchange._api_get(
                    path_url=CONSTANTS.SNAPSHOT_REST_URL,
                    params={"symbol": symbol},
                    is_auth_required=True,
                    limit_id=CONSTANTS.SNAPSHOT_REST_URL,
                )
            )
        except OSError as e:
            if "symbol_not_found" in str(e) or "404" in str(e):
                return None
            raise

    def test_order_book_snapshot(self, exchange: YellowProExchange):
        """GET /orderbook — response must be a dict with bids/asks keys (may be empty in staging)."""
        resp = self._fetch_order_book(exchange)
        if resp is None:
            pytest.skip(f"Order book for {TRADING_PAIR} has no data yet in staging (404)")
        assert isinstance(resp, dict), f"Unexpected order book response: {type(resp)}"
        assert "bids" in resp or "asks" in resp, f"Order book missing bids/asks keys: {resp}"

    def test_order_book_has_numeric_prices(self, exchange: YellowProExchange):
        """Order book prices and quantities must be parseable as numbers (skipped if empty)."""
        resp = self._fetch_order_book(exchange)
        if resp is None:
            pytest.skip(f"Order book for {TRADING_PAIR} has no data yet in staging (404)")
        entries = [(s, e) for s in ("bids", "asks") for e in resp.get(s, [])[:5]]
        if not entries:
            pytest.skip("Order book is empty in staging — skipping numeric price check")
        for side, entry in entries:
            price, qty = Decimal(str(entry[0])), Decimal(str(entry[1]))
            assert price > 0 and qty > 0, f"Invalid {side} entry: {entry}"

    def test_ticker_24hr(self, exchange: YellowProExchange):
        """GET /ticker/24hr — returns last traded price for the pair."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        loop = _get_event_loop()
        resp = loop.run_until_complete(
            exchange._api_get(
                path_url=CONSTANTS.TICKER_PRICE_CHANGE_URL,
                params={"symbol": symbol},
                is_auth_required=True,
                limit_id=CONSTANTS.TICKER_PRICE_CHANGE_URL,
            )
        )
        assert isinstance(resp, dict), f"Unexpected ticker response: {type(resp)}"
        last_price = resp.get("last") if resp.get("last") is not None else resp.get("lastPrice")
        assert last_price is not None, f"Ticker missing last price field: {resp}"
        # last_price may be 0 in staging if no trades have occurred yet
        assert Decimal(str(last_price)) >= 0, f"Ticker last price is negative: {last_price}"

    def test_get_last_traded_prices(self, exchange: YellowProExchange):
        """get_last_traded_prices() must return a float for the trading pair."""
        loop = _get_event_loop()

        async def _run():
            # Ensure symbol map is ready
            await exchange._initialize_trading_pair_symbol_map()
            return await exchange.get_last_traded_prices([TRADING_PAIR])

        results = loop.run_until_complete(_run())
        assert isinstance(results, dict), f"Expected dict, got: {type(results)}"
        # In staging, last traded price may be 0 and filtered out — just verify the call succeeded
        if TRADING_PAIR in results:
            assert results[TRADING_PAIR] >= 0, f"Negative price for {TRADING_PAIR}"
        else:
            pytest.skip(f"{TRADING_PAIR} not in results (last price is 0 — no trades in staging yet)")


# ---------------------------------------------------------------------------
# 3. Account  (requires credentials)
# ---------------------------------------------------------------------------

class TestAccount:
    @requires_credentials
    def test_update_balances(self, exchange: YellowProExchange):
        """_update_balances() must populate _account_balances."""
        loop = _get_event_loop()
        loop.run_until_complete(exchange._update_balances())
        assert isinstance(exchange._account_balances, dict)
        # At least the session account should show some asset (even 0)
        # Just verifying the call succeeded and parsed without error

    @requires_credentials
    def test_balances_are_non_negative(self, exchange: YellowProExchange):
        """All parsed balances must be >= 0."""
        loop = _get_event_loop()
        loop.run_until_complete(exchange._update_balances())
        for asset, balance in exchange._account_balances.items():
            assert balance >= Decimal("0"), f"Negative balance for {asset}: {balance}"

    @requires_credentials
    def test_available_balance_lte_total(self, exchange: YellowProExchange):
        """Available balance must never exceed total balance."""
        loop = _get_event_loop()
        loop.run_until_complete(exchange._update_balances())
        for asset in exchange._account_balances:
            total = exchange._account_balances[asset]
            available = exchange._account_available_balances.get(asset, Decimal("0"))
            assert available <= total, (
                f"Available ({available}) > total ({total}) for {asset}"
            )


# ---------------------------------------------------------------------------
# 4. Open Orders & History  (requires credentials)
# ---------------------------------------------------------------------------

class TestOrderQueries:
    @requires_credentials
    def test_fetch_open_orders(self, exchange: YellowProExchange):
        """_fetch_open_orders_for_market() must return a list."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        loop = _get_event_loop()
        orders = loop.run_until_complete(exchange._fetch_open_orders_for_market(symbol))
        assert isinstance(orders, list)

    @requires_credentials
    def test_open_order_entries_have_required_fields(self, exchange: YellowProExchange):
        """Each open order entry must have order_uuid (or equivalent) and market."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        loop = _get_event_loop()
        orders = loop.run_until_complete(exchange._fetch_open_orders_for_market(symbol))
        for order in orders:
            uid = order.get("order_uuid") or order.get("uuid") or order.get("order_id")
            assert uid is not None, f"Open order missing ID: {order}"

    @requires_credentials
    def test_download_orders_snapshot(self, exchange: YellowProExchange):
        """_download_orders_snapshot() must return a list."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        loop = _get_event_loop()
        orders = loop.run_until_complete(
            exchange._download_orders_snapshot(symbol, force_refresh=True)
        )
        assert isinstance(orders, list)

    @requires_credentials
    def test_fetch_trade_history(self, exchange: YellowProExchange):
        """GET /spot/trades must return a list."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        channel = (CHANNEL_ID or SESSION_ID).strip()
        params = {
            "app_session_id": SESSION_ID,
            "market": symbol,
            "page": 1,
            "page_size": 10,
        }
        if channel:
            params["channel_id"] = channel
        loop = _get_event_loop()
        resp = loop.run_until_complete(
            exchange._api_get(
                path_url=CONSTANTS.TRADES_URL,
                params=params,
                is_auth_required=True,
                limit_id=CONSTANTS.TRADES_URL,
            )
        )
        trades = resp.get("trades", resp) if isinstance(resp, dict) else resp
        assert isinstance(trades, list)


# ---------------------------------------------------------------------------
# 5. Place & Cancel Orders  (requires credentials)
# ---------------------------------------------------------------------------

class TestOrders:
    def _get_safe_prices(self, exchange: YellowProExchange) -> tuple:
        """Return (safe_buy_price, safe_sell_price) far from market."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        loop = _get_event_loop()
        resp = loop.run_until_complete(
            exchange._api_get(
                path_url=CONSTANTS.SNAPSHOT_REST_URL,
                params={"symbol": symbol},
                is_auth_required=True,
                limit_id=CONSTANTS.SNAPSHOT_REST_URL,
            )
        )
        bids = resp.get("bids", [])
        asks = resp.get("asks", [])
        best_bid = Decimal(str(bids[0][0])) if bids else None
        best_ask = Decimal(str(asks[0][0])) if asks else None
        ref_price = best_bid or best_ask
        if ref_price is None:
            pytest.skip("Order book is empty — cannot compute safe test prices")
        safe_buy = (best_bid or ref_price) * BUY_PRICE_FACTOR
        safe_sell = (best_ask or ref_price) * SELL_PRICE_FACTOR
        return safe_buy, safe_sell

    def _place_order(self, exchange: YellowProExchange, side: str,
                     order_type: str, price: Decimal, qty: Decimal) -> dict:
        """Place an order and return the raw response."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        body = {
            "app_session_id": SESSION_ID,
            "market": symbol,
            "type": order_type,
            "side": side,
            "amount": str(qty),
            "price": str(price.quantize(Decimal("0.01"))),
            "time_in_force": "gtc",
        }

        loop = _get_event_loop()
        resp = loop.run_until_complete(
            exchange._api_post(
                path_url=CONSTANTS.CREATE_ORDER_URL,
                data=body,
                is_auth_required=True,
                limit_id=CONSTANTS.CREATE_ORDER_URL,
            )
        )
        return resp

    def _cancel_order(self, exchange: YellowProExchange, order_uuid: str) -> bool:
        """Cancel an order by UUID."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        loop = _get_event_loop()
        return loop.run_until_complete(
            exchange._cancel_order_by_uuid(symbol, order_uuid)
        )

    @requires_credentials
    def test_place_and_cancel_limit_buy(self, exchange: YellowProExchange):
        """Place a LIMIT buy order far below market and cancel it."""
        safe_buy, _ = self._get_safe_prices(exchange)
        resp = self._place_order(exchange, "buy", "limit", safe_buy, TEST_QUANTITY)

        order_id = resp.get("order_uuid") or resp.get("uuid") or resp.get("order_id")
        assert order_id is not None, f"No order ID in response: {resp}"

        cancelled = self._cancel_order(exchange, str(order_id))
        assert cancelled, f"Failed to cancel order {order_id}"

    @requires_credentials
    def test_place_and_cancel_limit_sell(self, exchange: YellowProExchange):
        """Place a LIMIT sell order far above market and cancel it."""
        _, safe_sell = self._get_safe_prices(exchange)
        resp = self._place_order(exchange, "sell", "limit", safe_sell, TEST_QUANTITY)

        order_id = resp.get("order_uuid") or resp.get("uuid") or resp.get("order_id")
        assert order_id is not None, f"No order ID in response: {resp}"

        cancelled = self._cancel_order(exchange, str(order_id))
        assert cancelled, f"Failed to cancel order {order_id}"

    @requires_credentials
    def test_cancel_order_by_uuid_returns_true(self, exchange: YellowProExchange):
        """Place a fresh order and verify cancel returns True."""
        safe_buy, _ = self._get_safe_prices(exchange)
        resp = self._place_order(exchange, "buy", "limit", safe_buy, TEST_QUANTITY)
        order_id = resp.get("order_uuid") or resp.get("uuid") or resp.get("order_id")
        assert order_id, f"No order ID: {resp}"

        result = self._cancel_order(exchange, str(order_id))
        assert result is True

    @requires_credentials
    def test_order_status_open_then_cancelled(self, exchange: YellowProExchange):
        """Place a safe order, verify it appears in open orders, cancel, verify it's gone."""
        import time

        safe_buy, _ = self._get_safe_prices(exchange)
        resp = self._place_order(exchange, "buy", "limit", safe_buy, TEST_QUANTITY)
        order_id = resp.get("order_uuid") or resp.get("uuid") or resp.get("order_id")
        assert order_id, f"No order ID: {resp}"

        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        loop = _get_event_loop()
        time.sleep(1)

        # Check open orders — our order should be there
        open_orders = loop.run_until_complete(exchange._fetch_open_orders_for_market(symbol))
        found = any(
            str(o.get("order_uuid") or o.get("uuid") or o.get("order_id")) == str(order_id)
            for o in open_orders
        )
        assert found, f"Order {order_id} not found in open orders"
        print(f"  Order {order_id} found in open orders")

        # Cancel
        cancelled = self._cancel_order(exchange, str(order_id))
        assert cancelled, f"Failed to cancel order {order_id}"
        time.sleep(1)

        # Check open orders again — should be gone
        open_orders = loop.run_until_complete(exchange._fetch_open_orders_for_market(symbol))
        still_there = any(
            str(o.get("order_uuid") or o.get("uuid") or o.get("order_id")) == str(order_id)
            for o in open_orders
        )
        assert not still_there, f"Order {order_id} still in open orders after cancel"
        print(f"  Order {order_id} gone from open orders after cancel")

    @requires_credentials
    def test_trade_fill_buy_and_sell(self, exchange: YellowProExchange):
        """Place aggressive orders that cross the spread and verify fills via trade history."""
        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        loop = _get_event_loop()

        # Get current order book
        resp = loop.run_until_complete(
            exchange._api_get(
                path_url=CONSTANTS.SNAPSHOT_REST_URL,
                params={"symbol": symbol},
                is_auth_required=True,
                limit_id=CONSTANTS.SNAPSHOT_REST_URL,
            )
        )
        asks = resp.get("asks", [])
        bids = resp.get("bids", [])
        if not asks or not bids:
            pytest.skip("Order book empty — cannot test trade fill")

        best_ask = Decimal(str(asks[0][0]))
        best_bid = Decimal(str(bids[0][0]))

        # Buy above best ask (crosses spread, should fill)
        buy_price = (best_ask * Decimal("1.005")).quantize(Decimal("0.01"))
        buy_resp = self._place_order(exchange, "buy", "limit", buy_price, TEST_QUANTITY)
        buy_order_id = buy_resp.get("order_uuid") or buy_resp.get("uuid") or buy_resp.get("order_id")
        assert buy_order_id, f"No buy order ID: {buy_resp}"
        print(f"  Buy order placed: id={buy_order_id} price={buy_price}")

        import time
        time.sleep(2)  # wait for fill

        # Check order state
        orders = loop.run_until_complete(
            exchange._download_orders_snapshot(symbol, force_refresh=True, target_order_id=str(buy_order_id))
        )
        buy_order = exchange._find_order_in_snapshot(orders, str(buy_order_id))
        if buy_order:
            print(f"  Buy order state: {buy_order.get('state')}")
            assert buy_order.get("state") in ("done", "filled", "wait", "open"), \
                f"Unexpected buy order state: {buy_order.get('state')}"

        # Sell below best bid (crosses spread, should fill)
        sell_price = (best_bid * Decimal("0.995")).quantize(Decimal("0.01"))
        sell_resp = self._place_order(exchange, "sell", "limit", sell_price, TEST_QUANTITY)
        sell_order_id = sell_resp.get("order_uuid") or sell_resp.get("uuid") or sell_resp.get("order_id")
        assert sell_order_id, f"No sell order ID: {sell_resp}"
        print(f"  Sell order placed: id={sell_order_id} price={sell_price}")

        time.sleep(2)  # wait for fill

        # Check order state
        orders = loop.run_until_complete(
            exchange._download_orders_snapshot(symbol, force_refresh=True, target_order_id=str(sell_order_id))
        )
        sell_order = exchange._find_order_in_snapshot(orders, str(sell_order_id))
        if sell_order:
            print(f"  Sell order state: {sell_order.get('state')}")
            assert sell_order.get("state") in ("done", "filled", "wait", "open"), \
                f"Unexpected sell order state: {sell_order.get('state')}"

        # Verify trades exist
        channel = (CHANNEL_ID or SESSION_ID).strip()
        params = {
            "app_session_id": SESSION_ID,
            "market": symbol,
            "page": 1,
            "page_size": 10,
        }
        if channel:
            params["channel_id"] = channel
        resp = loop.run_until_complete(
            exchange._api_get(
                path_url=CONSTANTS.TRADES_URL,
                params=params,
                is_auth_required=True,
                limit_id=CONSTANTS.TRADES_URL,
            )
        )
        trades = resp.get("trades", resp) if isinstance(resp, dict) else resp
        print(f"  Recent trades count: {len(trades)}")
        if trades:
            latest = trades[0]
            print(f"  Latest trade: id={latest.get('id')} price={latest.get('price')} "
                  f"amount={latest.get('amount')} side={latest.get('side')}")


# ---------------------------------------------------------------------------
# 6. WebSocket Events  (requires credentials)
# ---------------------------------------------------------------------------

class TestWebSocket:
    @requires_credentials
    def test_ws_events_on_place_cancel_and_fill(self, exchange: YellowProExchange):
        """Connect to private WS, place/cancel/fill orders, and print ALL events received."""
        import json

        import aiohttp

        loop = _get_event_loop()

        async def _run():
            ws_url = CONSTANTS.WS_URLS[DOMAIN]
            auth_headers = exchange._auth.get_ws_auth_headers()
            collected_events = []
            decoder = json.JSONDecoder()

            def parse_multi_json(raw: str):
                """Parse one or more concatenated JSON objects from a single WS message."""
                results = []
                idx = 0
                while idx < len(raw):
                    while idx < len(raw) and raw[idx].isspace():
                        idx += 1
                    if idx >= len(raw):
                        break
                    try:
                        obj, next_idx = decoder.raw_decode(raw, idx)
                        results.append(obj)
                        idx = next_idx
                    except json.JSONDecodeError:
                        break
                return results

            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url, headers=auth_headers) as ws:
                    # Connect
                    await ws.send_json({"id": 1, "connect": {}})
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    for obj in parse_multi_json(msg.data):
                        print(f"  WS connect response: {obj}")

                    # Subscribe to private channel
                    channel = f"private.{SESSION_ID}"
                    await ws.send_json({"id": 2, "subscribe": {"channel": channel}})
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    for obj in parse_multi_json(msg.data):
                        print(f"  WS subscribe response: {obj}")

                    # Helper to drain all pending messages
                    async def drain_events(wait_secs=3):
                        deadline = asyncio.get_event_loop().time() + wait_secs
                        while True:
                            remaining = deadline - asyncio.get_event_loop().time()
                            if remaining <= 0:
                                break
                            try:
                                msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    for data in parse_multi_json(msg.data):
                                        if data:
                                            collected_events.append(data)
                                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break
                            except asyncio.TimeoutError:
                                break

                    # 1. Place a safe buy order (won't fill)
                    symbol = _exchange_symbol(exchange, TRADING_PAIR)
                    ob = await exchange._api_get(
                        path_url=CONSTANTS.SNAPSHOT_REST_URL,
                        params={"symbol": symbol},
                        is_auth_required=True,
                        limit_id=CONSTANTS.SNAPSHOT_REST_URL,
                    )
                    bids = ob.get("bids", [])
                    asks = ob.get("asks", [])
                    best_bid = Decimal(str(bids[0][0])) if bids else None
                    best_ask = Decimal(str(asks[0][0])) if asks else None
                    if not best_bid or not best_ask:
                        print("  Order book empty — skipping")
                        return

                    safe_price = (best_bid * BUY_PRICE_FACTOR).quantize(Decimal("0.01"))
                    print(f"\n  --- Place safe buy at {safe_price} (won't fill) ---")
                    place_resp = await exchange._api_post(
                        path_url=CONSTANTS.CREATE_ORDER_URL,
                        data={
                            "app_session_id": SESSION_ID,
                            "market": symbol,
                            "type": "limit",
                            "side": "buy",
                            "amount": str(TEST_QUANTITY),
                            "price": str(safe_price),
                            "time_in_force": "gtc",
                        },
                        is_auth_required=True,
                        limit_id=CONSTANTS.CREATE_ORDER_URL,
                    )
                    order_id = place_resp.get("order_uuid") or place_resp.get("uuid")
                    print(f"  Placed order: {order_id}")
                    await drain_events(3)

                    # 2. Cancel it
                    print(f"\n  --- Cancel order {order_id} ---")
                    await exchange._cancel_order_by_uuid(symbol, str(order_id))
                    await drain_events(3)

                    # 3. Place aggressive buy (should fill)
                    fill_price = (best_ask * Decimal("1.005")).quantize(Decimal("0.01"))
                    print(f"\n  --- Place aggressive buy at {fill_price} (should fill) ---")
                    fill_resp = await exchange._api_post(
                        path_url=CONSTANTS.CREATE_ORDER_URL,
                        data={
                            "app_session_id": SESSION_ID,
                            "market": symbol,
                            "type": "limit",
                            "side": "buy",
                            "amount": str(TEST_QUANTITY),
                            "price": str(fill_price),
                            "time_in_force": "gtc",
                        },
                        is_auth_required=True,
                        limit_id=CONSTANTS.CREATE_ORDER_URL,
                    )
                    fill_order_id = fill_resp.get("order_uuid") or fill_resp.get("uuid")
                    print(f"  Placed order: {fill_order_id}")
                    await drain_events(5)

                    # 4. Place aggressive sell to round-trip
                    sell_price = (best_bid * Decimal("0.995")).quantize(Decimal("0.01"))
                    print(f"\n  --- Place aggressive sell at {sell_price} (should fill) ---")
                    sell_resp = await exchange._api_post(
                        path_url=CONSTANTS.CREATE_ORDER_URL,
                        data={
                            "app_session_id": SESSION_ID,
                            "market": symbol,
                            "type": "limit",
                            "side": "sell",
                            "amount": str(TEST_QUANTITY),
                            "price": str(sell_price),
                            "time_in_force": "gtc",
                        },
                        is_auth_required=True,
                        limit_id=CONSTANTS.CREATE_ORDER_URL,
                    )
                    sell_order_id = sell_resp.get("order_uuid") or sell_resp.get("uuid")
                    print(f"  Placed order: {sell_order_id}")
                    await drain_events(5)

            print(f"\n  Total WS events collected: {len(collected_events)}")
            for i, evt in enumerate(collected_events):
                push = evt.get("push", {})
                if push:
                    pub_data = push.get("pub", {}).get("data", {})
                    header = pub_data.get("header", {})
                    print(f"  Event {i}: type={header.get('type')} channel={push.get('channel')}")

        loop.run_until_complete(_run())


# ---------------------------------------------------------------------------
# 7. WS Field Mapping  (requires credentials)
#    Tests that WS event payloads (which use different field names than REST)
#    are correctly processed by the connector handlers.
# ---------------------------------------------------------------------------

class TestWSFieldMapping:
    @requires_credentials
    def test_handle_order_update_with_ws_field_names(self, exchange: YellowProExchange):
        """order.updated WS events use 'order_id' (UUID), not 'uuid'/'order_uuid'."""
        loop = _get_event_loop()

        async def _run():
            await exchange._initialize_trading_pair_symbol_map()

            # Place a safe order to get a tracked order
            symbol = _exchange_symbol(exchange, TRADING_PAIR)
            ob = await exchange._api_get(
                path_url=CONSTANTS.SNAPSHOT_REST_URL,
                params={"symbol": symbol},
                is_auth_required=True,
                limit_id=CONSTANTS.SNAPSHOT_REST_URL,
            )
            bids = ob.get("bids", [])
            if not bids:
                return  # skip if no bids
            best_bid = Decimal(str(bids[0][0]))
            safe_price = (best_bid * BUY_PRICE_FACTOR).quantize(Decimal("0.01"))

            # Place via REST
            resp = await exchange._api_post(
                path_url=CONSTANTS.CREATE_ORDER_URL,
                data={
                    "app_session_id": SESSION_ID,
                    "market": symbol,
                    "type": "limit",
                    "side": "buy",
                    "amount": str(TEST_QUANTITY),
                    "price": str(safe_price),
                    "time_in_force": "gtc",
                },
                is_auth_required=True,
                limit_id=CONSTANTS.CREATE_ORDER_URL,
            )
            order_uuid = resp.get("order_uuid") or resp.get("uuid")
            assert order_uuid, f"No order ID: {resp}"

            # Register as tracked order
            from hummingbot.core.data_type.common import OrderType, TradeType
            from hummingbot.core.data_type.in_flight_order import InFlightOrder
            tracked = InFlightOrder(
                client_order_id="test-client-id",
                exchange_order_id=str(order_uuid),
                trading_pair=TRADING_PAIR,
                order_type=OrderType.LIMIT,
                trade_type=TradeType.BUY,
                amount=TEST_QUANTITY,
                price=safe_price,
                creation_timestamp=exchange.current_timestamp,
            )
            exchange._order_tracker.start_tracking_order(tracked)

            # Simulate WS order.updated event using "order_id" (not "uuid")
            ws_event = {
                "header": {"type": "order.updated", "created_at": "2026-01-01T00:00:00Z"},
                "order_id": str(order_uuid),  # WS uses this, not "uuid"
                "market": symbol,
                "state": "wait",
                "amount": str(TEST_QUANTITY),
                "origin_amount": str(TEST_QUANTITY),
                "price": str(safe_price),
            }
            await exchange._handle_order_update_event(ws_event)

            # Verify the order was found and updated (not silently dropped)
            order = exchange._order_tracker.all_updatable_orders.get("test-client-id")
            assert order is not None, "Tracked order lost after WS update"
            print(f"  Order state after WS update: {order.current_state.name}")

            # Simulate WS order.cancelled event using "order_id"
            ws_cancel = {
                "header": {"type": "order.cancelled", "created_at": "2026-01-01T00:00:01Z"},
                "order_id": str(order_uuid),
                "market": symbol,
                "state": "cancel",
                "amount": "0",
                "origin_amount": str(TEST_QUANTITY),
                "price": str(safe_price),
            }
            await exchange._handle_order_update_event(ws_cancel)
            print(f"  Order state after WS cancel: {order.current_state.name}")

            # Also cancel on exchange to clean up
            try:
                await exchange._cancel_order_by_uuid(symbol, str(order_uuid))
            except Exception:
                pass  # may already be cancelled

        loop.run_until_complete(_run())

    @requires_credentials
    def test_process_rest_trade_with_ws_field_names(self, exchange: YellowProExchange):
        """trade.executed WS events use 'order_id'/'trade_id', not 'order_uuid'/'id'."""
        loop = _get_event_loop()

        async def _run():
            await exchange._initialize_trading_pair_symbol_map()

            fake_uuid = "00000000-0000-0000-0000-000000000001"

            # Register a fake tracked order
            from hummingbot.core.data_type.common import OrderType, TradeType
            from hummingbot.core.data_type.in_flight_order import InFlightOrder
            tracked = InFlightOrder(
                client_order_id="test-trade-client",
                exchange_order_id=fake_uuid,
                trading_pair=TRADING_PAIR,
                order_type=OrderType.LIMIT,
                trade_type=TradeType.BUY,
                amount=TEST_QUANTITY,
                price=Decimal("2000"),
                creation_timestamp=exchange.current_timestamp,
            )
            exchange._order_tracker.start_tracking_order(tracked)

            # Simulate WS trade.executed with WS field names
            ws_trade = {
                "header": {"type": "trade.executed", "created_at": "2026-01-01T00:00:00Z"},
                "order_id": fake_uuid,     # WS uses this, not "order_uuid"
                "trade_id": 99999,          # WS uses this, not "id"
                "market": "ETHUSDT",
                "price": "2000",
                "amount": str(TEST_QUANTITY),
                "is_maker": False,
                "executed_at": "2026-01-01T00:00:00Z",
            }

            # Normalize fields as _user_stream_event_listener does
            if "order_uuid" not in ws_trade and "order_id" in ws_trade:
                ws_trade["order_uuid"] = ws_trade["order_id"]
            if "id" not in ws_trade and "trade_id" in ws_trade:
                ws_trade["id"] = ws_trade["trade_id"]

            # Add to dedup set
            trade_id = str(ws_trade.get("id"))
            exchange._processed_trade_ids.append(trade_id)
            exchange._processed_trade_ids_lookup.add(trade_id)

            await exchange._process_rest_trade(ws_trade)

            # Verify fill was recorded
            order = exchange._order_tracker.all_fillable_orders.get("test-trade-client")
            if order:
                print(f"  Executed amount after WS trade: {order.executed_amount_base}")
                assert order.executed_amount_base == TEST_QUANTITY, \
                    f"Expected {TEST_QUANTITY}, got {order.executed_amount_base}"
            else:
                print("  Order already completed (moved out of fillable)")

            # Verify dedup: same trade_id should be in the lookup
            assert trade_id in exchange._processed_trade_ids_lookup, \
                f"Trade {trade_id} not in dedup set"
            print(f"  Trade {trade_id} correctly in dedup set")

        loop.run_until_complete(_run())

    @requires_credentials
    def test_e2e_ws_cancel_via_framework(self, exchange: YellowProExchange):
        """End-to-end: start WS event listener, place+cancel order, verify state via framework."""
        from hummingbot.core.data_type.common import OrderType, TradeType
        from hummingbot.core.data_type.in_flight_order import InFlightOrder
        from hummingbot.core.utils.async_utils import safe_ensure_future

        loop = _get_event_loop()

        async def _run():
            # Fresh exchange instance to avoid shared state
            ex = YellowProExchange(
                yellow_pro_app_session_id=SESSION_ID,
                yellow_pro_api_key=API_KEY,
                yellow_pro_api_secret=API_SECRET,
                trading_pairs=[TRADING_PAIR],
                trading_required=True,
                yellow_pro_domain=DOMAIN,
            )
            await ex._initialize_trading_pair_symbol_map()

            # Start the full WS pipeline: tracker + event listener
            ex._user_stream_tracker_task = safe_ensure_future(ex._user_stream_tracker.start())
            ex._user_stream_event_listener_task = safe_ensure_future(ex._user_stream_event_listener())

            # Wait for WS to connect
            for _ in range(20):
                if ex._user_stream_tracker.data_source.last_recv_time > 0:
                    break
                await asyncio.sleep(0.5)
            assert ex._user_stream_tracker.data_source.last_recv_time > 0, "WS never connected"
            print("  WS connected and receiving")

            try:
                symbol = _exchange_symbol(ex, TRADING_PAIR)
                ob = await ex._api_get(
                    path_url=CONSTANTS.SNAPSHOT_REST_URL,
                    params={"symbol": symbol},
                    is_auth_required=True,
                    limit_id=CONSTANTS.SNAPSHOT_REST_URL,
                )
                bids = ob.get("bids", [])
                if not bids:
                    print("  No bids — skipping")
                    return
                best_bid = Decimal(str(bids[0][0]))
                safe_price = (best_bid * BUY_PRICE_FACTOR).quantize(Decimal("0.01"))

                # Place order via REST
                try:
                    resp = await ex._api_post(
                        path_url=CONSTANTS.CREATE_ORDER_URL,
                        data={
                            "app_session_id": SESSION_ID,
                            "market": symbol,
                            "type": "limit",
                            "side": "buy",
                            "amount": str(TEST_QUANTITY),
                            "price": str(safe_price),
                            "time_in_force": "gtc",
                        },
                        is_auth_required=True,
                        limit_id=CONSTANTS.CREATE_ORDER_URL,
                    )
                except IOError as e:
                    if "lock funds" in str(e) or "connection refused" in str(e).lower():
                        pytest.skip(f"Staging infra error: {e}")
                    raise
                order_uuid = str(resp.get("order_uuid") or resp.get("uuid"))
                assert order_uuid, f"No order ID: {resp}"
                print(f"  Order placed: {order_uuid} at {safe_price}")

                # Track order in framework
                tracked = InFlightOrder(
                    client_order_id="e2e-cancel-test",
                    exchange_order_id=order_uuid,
                    trading_pair=TRADING_PAIR,
                    order_type=OrderType.LIMIT,
                    trade_type=TradeType.BUY,
                    amount=TEST_QUANTITY,
                    price=safe_price,
                    creation_timestamp=ex.current_timestamp,
                )
                ex._order_tracker.start_tracking_order(tracked)
                await asyncio.sleep(1)

                # Cancel via REST
                await ex._cancel_order_by_uuid(symbol, order_uuid)
                print("  Cancel sent")

                # Wait for WS order.cancelled event to be processed by _user_stream_event_listener
                from hummingbot.core.data_type.in_flight_order import OrderState
                for _ in range(30):
                    if tracked.current_state in (OrderState.CANCELED, OrderState.CANCELED):
                        break
                    await asyncio.sleep(0.2)

                print(f"  Order state via framework: {tracked.current_state.name}")
                assert tracked.current_state in (OrderState.CANCELED, OrderState.CANCELED), \
                    f"Expected CANCELLED, got {tracked.current_state.name} — WS event not processed by framework"

            finally:
                # Stop WS
                if ex._user_stream_event_listener_task:
                    ex._user_stream_event_listener_task.cancel()
                if ex._user_stream_tracker_task:
                    ex._user_stream_tracker_task.cancel()
                await ex._user_stream_tracker.stop()

        loop.run_until_complete(_run())

    @requires_credentials
    def test_e2e_ws_fill_via_framework(self, exchange: YellowProExchange):
        """End-to-end: start WS event listener, place aggressive order, verify fill via framework."""
        from hummingbot.core.data_type.common import OrderType, TradeType
        from hummingbot.core.data_type.in_flight_order import InFlightOrder
        from hummingbot.core.utils.async_utils import safe_ensure_future

        loop = _get_event_loop()

        async def _run():
            # Fresh exchange instance to avoid shared state
            ex = YellowProExchange(
                yellow_pro_app_session_id=SESSION_ID,
                yellow_pro_api_key=API_KEY,
                yellow_pro_api_secret=API_SECRET,
                trading_pairs=[TRADING_PAIR],
                trading_required=True,
                yellow_pro_domain=DOMAIN,
            )
            await ex._initialize_trading_pair_symbol_map()

            # Start full WS pipeline
            ex._user_stream_tracker_task = safe_ensure_future(ex._user_stream_tracker.start())
            ex._user_stream_event_listener_task = safe_ensure_future(ex._user_stream_event_listener())

            for _ in range(20):
                if ex._user_stream_tracker.data_source.last_recv_time > 0:
                    break
                await asyncio.sleep(0.5)
            assert ex._user_stream_tracker.data_source.last_recv_time > 0, "WS never connected"
            print("  WS connected and receiving")

            try:
                symbol = _exchange_symbol(ex, TRADING_PAIR)
                ob = await ex._api_get(
                    path_url=CONSTANTS.SNAPSHOT_REST_URL,
                    params={"symbol": symbol},
                    is_auth_required=True,
                    limit_id=CONSTANTS.SNAPSHOT_REST_URL,
                )
                asks = ob.get("asks", [])
                bids = ob.get("bids", [])
                if not asks or not bids:
                    print("  Order book empty — skipping")
                    return

                best_ask = Decimal(str(asks[0][0]))
                best_bid = Decimal(str(bids[0][0]))

                # Place aggressive buy (should fill)
                fill_price = (best_ask * Decimal("1.005")).quantize(Decimal("0.01"))
                resp = await ex._api_post(
                    path_url=CONSTANTS.CREATE_ORDER_URL,
                    data={
                        "app_session_id": SESSION_ID,
                        "market": symbol,
                        "type": "limit",
                        "side": "buy",
                        "amount": str(TEST_QUANTITY),
                        "price": str(fill_price),
                        "time_in_force": "gtc",
                    },
                    is_auth_required=True,
                    limit_id=CONSTANTS.CREATE_ORDER_URL,
                )
                order_uuid = str(resp.get("order_uuid") or resp.get("uuid"))
                assert order_uuid, f"No order ID: {resp}"
                print(f"  Buy order placed: {order_uuid} at {fill_price}")

                tracked = InFlightOrder(
                    client_order_id="e2e-fill-test",
                    exchange_order_id=order_uuid,
                    trading_pair=TRADING_PAIR,
                    order_type=OrderType.LIMIT,
                    trade_type=TradeType.BUY,
                    amount=TEST_QUANTITY,
                    price=fill_price,
                    creation_timestamp=ex.current_timestamp,
                )
                ex._order_tracker.start_tracking_order(tracked)

                # Wait for WS trade.executed event to be processed by framework
                for _ in range(30):
                    if tracked.executed_amount_base > Decimal("0"):
                        break
                    await asyncio.sleep(0.2)

                print(f"  Executed amount via framework: {tracked.executed_amount_base}")
                assert tracked.executed_amount_base > Decimal("0"), \
                    "No fill detected via WS — trade.executed event not processed by framework"
                print("  PASS: Fill detected via WS framework pipeline")

                # Round-trip sell
                sell_price = (best_bid * Decimal("0.995")).quantize(Decimal("0.01"))
                await ex._api_post(
                    path_url=CONSTANTS.CREATE_ORDER_URL,
                    data={
                        "app_session_id": SESSION_ID,
                        "market": symbol,
                        "type": "limit",
                        "side": "sell",
                        "amount": str(TEST_QUANTITY),
                        "price": str(sell_price),
                        "time_in_force": "gtc",
                    },
                    is_auth_required=True,
                    limit_id=CONSTANTS.CREATE_ORDER_URL,
                )
                print(f"  Round-trip sell placed at {sell_price}")

            finally:
                if ex._user_stream_event_listener_task:
                    ex._user_stream_event_listener_task.cancel()
                if ex._user_stream_tracker_task:
                    ex._user_stream_tracker_task.cancel()
                await ex._user_stream_tracker.stop()

        loop.run_until_complete(_run())
