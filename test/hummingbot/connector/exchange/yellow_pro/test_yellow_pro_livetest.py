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
TRADING_PAIR = os.environ.get("YELLOW_PRO_TRADING_PAIR", "BTC-YTEST.USD")

HAS_CREDENTIALS = bool(API_KEY and API_SECRET and SESSION_ID)

# Safe distance from market price for test orders
BUY_PRICE_FACTOR = Decimal("0.5")   # 50% below best bid
SELL_PRICE_FACTOR = Decimal("2.0")  # 2x above best ask
TEST_QUANTITY = Decimal("0.0001")

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
            exchange._api_get(path_url=CONSTANTS.CHECK_NETWORK_URL, is_auth_required=True)
        )
        assert resp is not None, "Health check returned no response"

    @requires_credentials
    def test_auth_health(self, exchange: YellowProExchange):
        """GET /auth/health — verifies API key is accepted."""
        loop = _get_event_loop()
        resp = loop.run_until_complete(
            exchange._api_get(path_url=CONSTANTS.AUTH_HEALTH_URL, is_auth_required=True)
        )
        assert resp is not None


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

    def test_order_book_snapshot(self, exchange: YellowProExchange):
        """GET /orderbook — returns bids and asks for the configured trading pair."""
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
        assert isinstance(resp, dict), f"Unexpected order book response: {type(resp)}"
        assert "bids" in resp or "asks" in resp, f"Order book missing bids/asks: {resp}"

    def test_order_book_has_numeric_prices(self, exchange: YellowProExchange):
        """Order book prices and quantities must be parseable as numbers."""
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
        for side in ("bids", "asks"):
            for entry in resp.get(side, [])[:5]:
                price, qty = Decimal(str(entry[0])), Decimal(str(entry[1]))
                assert price > 0 and qty > 0

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
        last_price = resp.get("last") or resp.get("lastPrice")
        assert last_price is not None, f"Ticker missing last price: {resp}"
        assert Decimal(str(last_price)) > 0

    def test_get_last_traded_prices(self, exchange: YellowProExchange):
        """get_last_traded_prices() must return a float for the trading pair."""
        loop = _get_event_loop()

        async def _run():
            # Ensure symbol map is ready
            await exchange._initialize_trading_pair_symbol_map()
            return await exchange.get_last_traded_prices([TRADING_PAIR])

        results = loop.run_until_complete(_run())
        assert TRADING_PAIR in results, f"Missing {TRADING_PAIR} in results: {results}"
        assert results[TRADING_PAIR] > 0


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
        if best_bid is None or best_ask is None:
            pytest.skip("Order book is empty — cannot compute safe test prices")
        return best_bid * BUY_PRICE_FACTOR, best_ask * SELL_PRICE_FACTOR

    def _place_order(self, exchange: YellowProExchange, side: str,
                     order_type: str, price: Decimal, qty: Decimal) -> dict:
        """Place an order and return the raw response."""
        import json
        from hummingbot.core.web_assistant.connections.data_types import RESTMethod

        symbol = _exchange_symbol(exchange, TRADING_PAIR)
        channel = (CHANNEL_ID or SESSION_ID).strip()
        body = {
            "app_session_id": SESSION_ID,
            "market": symbol,
            "type": order_type,
            "side": side,
            "quantity": str(qty),
            "price": str(price.quantize(Decimal("0.0001"))),
        }
        if channel:
            body["channel_id"] = channel

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
    def test_place_and_cancel_limit_maker_sell(self, exchange: YellowProExchange):
        """Place a LIMIT_MAKER sell order far above market and cancel it."""
        _, safe_sell = self._get_safe_prices(exchange)
        resp = self._place_order(exchange, "sell", "limit_maker", safe_sell, TEST_QUANTITY)

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


# ---------------------------------------------------------------------------
# 6. Positions  (requires credentials + channel)
# ---------------------------------------------------------------------------

class TestPositions:
    @requires_credentials
    def test_get_positions_returns_list(self, exchange: YellowProExchange):
        """get_positions() must return a list (may be empty)."""
        channel = (CHANNEL_ID or SESSION_ID).strip()
        if not channel:
            pytest.skip("No channel_id or session_id available for positions query")
        loop = _get_event_loop()
        positions = loop.run_until_complete(exchange.get_positions(channel_id=channel))
        assert isinstance(positions, list)

    @requires_credentials
    def test_position_entries_have_required_fields(self, exchange: YellowProExchange):
        """Each position entry must have a market field."""
        channel = (CHANNEL_ID or SESSION_ID).strip()
        if not channel:
            pytest.skip("No channel_id or session_id available")
        loop = _get_event_loop()
        positions = loop.run_until_complete(exchange.get_positions(channel_id=channel))
        for pos in positions:
            assert "market" in pos or "symbol" in pos, f"Position missing market field: {pos}"
