"""
Yellow Pro Connector Demo

Runs a sequential set of live tests against the Yellow Pro exchange through
the Hummingbot connector interface, printing clear PASS/FAIL status for each step.

Start in Hummingbot:
    start --script yellow_pro_demo.py

The script runs once through all tests, then stops. Order tests are only run
when the connector is connected with API credentials (trading_required=True).
"""

import logging
import os
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, OrderType, TradeType
from hummingbot.core.event.events import (
    BuyOrderCancelledEvent,
    BuyOrderCreatedEvent,
    MarketOrderFailureEvent,
    OrderCancelledEvent,
    SellOrderCancelledEvent,
    SellOrderCreatedEvent,
)
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

CONNECTOR = "yellow_pro"

# Safe test order distances from market
BUY_PRICE_FACTOR = Decimal("0.5")   # 50% below best bid — will not fill
SELL_PRICE_FACTOR = Decimal("2.0")  # 2x above best ask — will not fill
TEST_AMOUNT = Decimal("0.0001")

# State machine phases
PHASE_WAIT_READY = "wait_ready"
PHASE_MARKET_DATA = "market_data"
PHASE_ACCOUNT = "account"
PHASE_ORDER_BUY = "order_buy"
PHASE_ORDER_BUY_CANCEL = "order_buy_cancel"
PHASE_ORDER_SELL = "order_sell"
PHASE_ORDER_SELL_CANCEL = "order_sell_cancel"
PHASE_DONE = "done"


class YellowProDemoConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: List[str] = []
    trading_pair: str = Field(
        default="BTC-YTEST.USD",
        description="Trading pair to use for the demo",
    )

    def update_markets(self, markets: MarketDict) -> MarketDict:
        markets[CONNECTOR] = markets.get(CONNECTOR, set()) | {self.trading_pair}
        return markets


class YellowProDemo(StrategyV2Base):
    """
    Sequential connector demo. Runs each test phase once, logs results, then stops.
    """

    def __init__(self, connectors: Dict[str, ConnectorBase], config: YellowProDemoConfig):
        super().__init__(connectors, config)
        self.config = config
        self._phase: str = PHASE_WAIT_READY
        self._phase_tick: int = 0          # ticks spent in current phase
        self._results: List[str] = []      # collected result lines
        self._pending_order_id: Optional[str] = None
        self._last_log: str = ""

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    def on_tick(self):
        self._phase_tick += 1
        connector = self.connectors.get(CONNECTOR)
        if connector is None:
            return

        if self._phase == PHASE_WAIT_READY:
            self._phase_wait_ready(connector)

        elif self._phase == PHASE_MARKET_DATA:
            self._phase_market_data(connector)

        elif self._phase == PHASE_ACCOUNT:
            self._phase_account(connector)

        elif self._phase == PHASE_ORDER_BUY:
            self._phase_order_buy(connector)

        elif self._phase == PHASE_ORDER_BUY_CANCEL:
            if self._phase_tick > 5:   # give 5 ticks for creation event
                self._log("  [TIMEOUT]  Did not receive buy order creation event — skipping cancel")
                self._results.append("SKIP  Place LIMIT buy order (no event received)")
                self._pending_order_id = None
                self._advance(PHASE_ORDER_SELL)

        elif self._phase == PHASE_ORDER_SELL:
            self._phase_order_sell(connector)

        elif self._phase == PHASE_ORDER_SELL_CANCEL:
            if self._phase_tick > 5:
                self._log("  [TIMEOUT]  Did not receive sell order creation event — skipping cancel")
                self._results.append("SKIP  Place LIMIT_MAKER sell order (no event received)")
                self._pending_order_id = None
                self._advance(PHASE_DONE)

        elif self._phase == PHASE_DONE:
            if self._phase_tick == 1:
                self._print_summary()

    # ------------------------------------------------------------------
    # Phase handlers
    # ------------------------------------------------------------------

    def _phase_wait_ready(self, connector: ConnectorBase):
        if self._phase_tick == 1:
            self._log("Yellow Pro Demo starting — waiting for connector to be ready...")

        if connector.ready:
            self._log("  Connector ready.")
            self._advance(PHASE_MARKET_DATA)
        elif self._phase_tick > 30:
            self._log("  [FAIL]  Connector did not become ready after 30s — aborting.")
            self._results.append("FAIL  Connector ready check")
            self._advance(PHASE_DONE)

    def _phase_market_data(self, connector: ConnectorBase):
        if self._phase_tick != 1:
            return

        trading_pair = self.config.trading_pair
        self._log("")
        self._log("─── Market Data ─────────────────────────────────")

        # Order book
        self._log("  Testing order book...")
        try:
            ob = connector.get_order_book(trading_pair)
            bids = list(ob.bid_entries())
            asks = list(ob.ask_entries())
            if bids or asks:
                self._ok("Order book", f"{len(bids)} bids, {len(asks)} asks")
            else:
                self._fail("Order book", "empty (no bids or asks)")
        except Exception as e:
            self._fail("Order book", e)

        # Best bid / ask
        self._log("  Testing best bid / ask...")
        try:
            best_bid = connector.get_price(trading_pair, False)
            best_ask = connector.get_price(trading_pair, True)
            if best_bid and best_ask:
                self._ok("Best bid/ask", f"bid={best_bid}  ask={best_ask}")
            else:
                self._fail("Best bid/ask", f"returned bid={best_bid} ask={best_ask}")
        except Exception as e:
            self._fail("Best bid/ask", e)

        # Mid price
        self._log("  Testing mid price...")
        try:
            mid = connector.get_mid_price(trading_pair)
            if mid and mid > 0:
                self._ok("Mid price", str(mid))
            else:
                self._fail("Mid price", f"returned {mid}")
        except Exception as e:
            self._fail("Mid price", e)

        # Trading rules
        self._log("  Testing trading rules...")
        try:
            rules = connector.trading_rules
            rule = rules.get(trading_pair)
            if rule:
                self._ok("Trading rules", f"min_size={rule.min_order_size}  min_price_increment={rule.min_price_increment}")
            else:
                self._fail("Trading rules", f"no rule for {trading_pair}")
        except Exception as e:
            self._fail("Trading rules", e)

        self._advance(PHASE_ACCOUNT)

    def _phase_account(self, connector: ConnectorBase):
        if self._phase_tick != 1:
            return

        self._log("")
        self._log("─── Account ─────────────────────────────────────")

        # Determine base/quote from trading pair
        parts = self.config.trading_pair.split("-")
        base = parts[0] if parts else "BTC"
        quote = parts[1] if len(parts) > 1 else "USDT"

        self._log("  Testing balances...")
        try:
            base_bal = connector.get_balance(base)
            quote_bal = connector.get_balance(quote)
            base_avail = connector.get_available_balance(base)
            quote_avail = connector.get_available_balance(quote)
            self._ok("Balances",
                     f"{base}={base_bal} (avail={base_avail})  "
                     f"{quote}={quote_bal} (avail={quote_avail})")
        except Exception as e:
            self._fail("Balances", e)

        # Decide whether to run order tests
        if not connector.trading_required:
            self._log("  Connector is read-only (no API key) — skipping order tests.")
            self._results.append("SKIP  Order tests (no credentials)")
            self._advance(PHASE_DONE)
        else:
            self._advance(PHASE_ORDER_BUY)

    def _phase_order_buy(self, connector: ConnectorBase):
        if self._phase_tick != 1:
            return

        self._log("")
        self._log("─── Orders ──────────────────────────────────────")
        self._log("  Testing LIMIT buy order (far below market)...")

        trading_pair = self.config.trading_pair
        try:
            best_bid = connector.get_price(trading_pair, False)
            if not best_bid or best_bid <= 0:
                self._fail("Place LIMIT buy order", "could not get best bid price")
                self._advance(PHASE_ORDER_SELL)
                return

            price = best_bid * BUY_PRICE_FACTOR
            price = connector.quantize_order_price(trading_pair, price)
            amount = connector.quantize_order_amount(trading_pair, TEST_AMOUNT)

            order_id = self.buy(
                connector_name=CONNECTOR,
                trading_pair=trading_pair,
                amount=amount,
                order_type=OrderType.LIMIT,
                price=price,
            )
            self._pending_order_id = order_id
            self._log(f"  Order submitted: id={order_id} price={price} qty={amount}")
            self._advance(PHASE_ORDER_BUY_CANCEL)

        except Exception as e:
            self._fail("Place LIMIT buy order", e)
            self._advance(PHASE_ORDER_SELL)

    def _phase_order_sell(self, connector: ConnectorBase):
        if self._phase_tick != 1:
            return

        self._log("  Testing LIMIT_MAKER sell order (far above market)...")

        trading_pair = self.config.trading_pair
        try:
            best_ask = connector.get_price(trading_pair, True)
            if not best_ask or best_ask <= 0:
                self._fail("Place LIMIT_MAKER sell order", "could not get best ask price")
                self._advance(PHASE_DONE)
                return

            price = best_ask * SELL_PRICE_FACTOR
            price = connector.quantize_order_price(trading_pair, price)
            amount = connector.quantize_order_amount(trading_pair, TEST_AMOUNT)

            order_id = self.sell(
                connector_name=CONNECTOR,
                trading_pair=trading_pair,
                amount=amount,
                order_type=OrderType.LIMIT_MAKER,
                price=price,
            )
            self._pending_order_id = order_id
            self._log(f"  Order submitted: id={order_id} price={price} qty={amount}")
            self._advance(PHASE_ORDER_SELL_CANCEL)

        except Exception as e:
            self._fail("Place LIMIT_MAKER sell order", e)
            self._advance(PHASE_DONE)

    # ------------------------------------------------------------------
    # Order event handlers
    # ------------------------------------------------------------------

    def did_create_buy_order(self, event: BuyOrderCreatedEvent):
        if self._phase == PHASE_ORDER_BUY_CANCEL:
            self._ok("Place LIMIT buy order", f"order_id={event.order_id}")
            self._log("  Cancelling buy order...")
            self.cancel(CONNECTOR, event.trading_pair, event.order_id)

    def did_create_sell_order(self, event: SellOrderCreatedEvent):
        if self._phase == PHASE_ORDER_SELL_CANCEL:
            self._ok("Place LIMIT_MAKER sell order", f"order_id={event.order_id}")
            self._log("  Cancelling sell order...")
            self.cancel(CONNECTOR, event.trading_pair, event.order_id)

    def did_cancel_order(self, event: OrderCancelledEvent):
        self._handle_cancel(event.order_id)

    def did_cancel_buy_order(self, event: BuyOrderCancelledEvent):
        self._handle_cancel(event.order_id)

    def did_cancel_sell_order(self, event: SellOrderCancelledEvent):
        self._handle_cancel(event.order_id)

    def did_fail_order(self, event: MarketOrderFailureEvent):
        if self._phase in (PHASE_ORDER_BUY_CANCEL, PHASE_ORDER_SELL_CANCEL):
            label = "Place LIMIT buy order" if self._phase == PHASE_ORDER_BUY_CANCEL else "Place LIMIT_MAKER sell order"
            self._fail(label, f"order failed: {event.order_id}")
            self._pending_order_id = None
            next_phase = PHASE_ORDER_SELL if self._phase == PHASE_ORDER_BUY_CANCEL else PHASE_DONE
            self._advance(next_phase)

    def _handle_cancel(self, order_id: str):
        if self._phase == PHASE_ORDER_BUY_CANCEL:
            self._ok("Cancel LIMIT buy order", f"order_id={order_id}")
            self._pending_order_id = None
            self._advance(PHASE_ORDER_SELL)
        elif self._phase == PHASE_ORDER_SELL_CANCEL:
            self._ok("Cancel LIMIT_MAKER sell order", f"order_id={order_id}")
            self._pending_order_id = None
            self._advance(PHASE_DONE)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _advance(self, phase: str):
        self._phase = phase
        self._phase_tick = 0

    def _log(self, msg: str):
        self._last_log = msg
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)

    def _ok(self, label: str, detail: str = ""):
        msg = f"  [PASS]  {label}" + (f"  —  {detail}" if detail else "")
        self._results.append(f"PASS  {label}")
        self._log(msg)

    def _fail(self, label: str, error):
        msg = f"  [FAIL]  {label}  —  {error}"
        self._results.append(f"FAIL  {label}")
        self._log(msg)

    def _print_summary(self):
        passed = sum(1 for r in self._results if r.startswith("PASS"))
        failed = sum(1 for r in self._results if r.startswith("FAIL"))
        skipped = sum(1 for r in self._results if r.startswith("SKIP"))

        self._log("")
        self._log("═══ Demo Complete ════════════════════════════════")
        for r in self._results:
            self._log(f"  {r}")
        self._log("─────────────────────────────────────────────────")
        self._log(f"  {passed} passed  |  {failed} failed  |  {skipped} skipped")
        self._log("═════════════════════════════════════════════════")

        if failed == 0:
            self._log("  All tests passed. You can stop the script.")
        else:
            self._log(f"  {failed} test(s) failed. Check logs for details.")

    def format_status(self) -> str:
        lines = [
            "Yellow Pro Demo",
            f"  Pair   : {self.config.trading_pair}",
            f"  Phase  : {self._phase}",
            f"  Status : {self._last_log.strip()}",
            "",
        ]
        if self._results:
            lines.append("Results so far:")
            for r in self._results:
                lines.append(f"  {r}")
        return "\n".join(lines)
