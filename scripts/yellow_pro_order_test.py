import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict

from hummingbot.client.hummingbot_application import HummingbotApplication
from hummingbot.core.data_type.common import OrderType
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    MarketOrderFailureEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent,
)
from hummingbot.connector.utils import split_hb_trading_pair
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


@dataclass
class MarketState:
    best_bid: Decimal | None
    best_ask: Decimal | None
    timestamp: float


@dataclass
class PerformanceMetrics:
    order_creation_latency: Dict[str, float]
    order_fill_latency: Dict[str, float]
    cancel_latency: float
    market_data_latency: float


class YellowProOrderSmokeTest(ScriptStrategyBase):
    """
    Comprehensive integration test script for the YellowPro spot connector.

    The script performs the following checks when started:
        1. Subscribes to market data and logs best bid / ask deltas over time.
        2. Places resting limit BUY and SELL orders, confirming creation, snapshots, and cancellation.
        3. Tests market BUY and SELL orders to verify fills and position deltas.
        4. Performs error handling tests (insufficient balance, invalid orders).
        5. Validates data consistency across order book, balances, and trade history.
        6. Measures performance metrics (latency, throughput).

    Adjust the class attributes below to fit the wallet session you are testing.
    """

    exchange = "yellow_pro"
    trading_pair = "BTC-YTEST.USD"
    order_amount = Decimal("0.01")
    price_offset = Decimal("0.01")  # Offset below/above bid/ask to keep maker orders resting
    cancel_delay = 10.0  # seconds
    market_snapshot_interval = 3.0
    open_order_snapshot_interval = 5.0
    position_snapshot_interval = 15.0
    fill_size_tolerance_pct = Decimal("0.02")  # allow 2% slippage between expected vs recorded fills

    # Enhanced test configuration
    test_sell_orders = True  # Enable sell order testing
    test_error_handling = True  # Enable error scenario tests
    test_performance = True  # Enable performance metrics collection
    test_data_consistency = True  # Enable data consistency validation
    max_concurrent_orders = 5  # Maximum concurrent orders for stress testing
    order_retry_attempts = 3  # Number of retry attempts for failed operations

    markets = {exchange: {trading_pair}}

    def __init__(self, connectors, config=None):
        super().__init__(connectors, config)
        self._base_asset, self._quote_asset = split_hb_trading_pair(self.trading_pair)

        # Buy order tracking
        self._passive_buy_order_id: str | None = None
        self._market_buy_order_id: str | None = None

        # Sell order tracking
        self._passive_sell_order_id: str | None = None
        self._market_sell_order_id: str | None = None

        # Common tracking
        self._order_tags: Dict[str, str] = {}
        self._creation_timestamps: Dict[str, float] = {}
        self._cancel_requested: Dict[str, bool] = {}

        # Test completion flags
        self._passive_buy_test_completed: bool = False
        self._passive_sell_test_completed: bool = False
        self._market_buy_test_completed: bool = False
        self._market_sell_test_completed: bool = False
        self._error_test_completed: bool = False

        # Performance tracking
        self._performance_metrics = PerformanceMetrics(
            order_creation_latency={},
            order_fill_latency={},
            cancel_latency=0.0,
            market_data_latency=0.0
        )
        self._order_start_times: Dict[str, float] = {}

        # Error handling
        self._retry_counts: Dict[str, int] = defaultdict(int)

        # Data validation
        self._initial_balances: Dict[str, Decimal] | None = None
        self._last_logged_balances: Dict[str, Decimal] | None = None
        self._last_balance_snapshot: float = 0.0
        self._last_open_orders_snapshot: float = 0.0
        self._last_market_snapshot: float = 0.0
        self._last_best_bid: Decimal | None = None
        self._last_best_ask: Decimal | None = None
        self._market_state_markers: Dict[str, MarketState] = {}
        zero_decimal = lambda: Decimal("0")
        self._fill_sizes = defaultdict(zero_decimal)
        self._fill_quotes = defaultdict(zero_decimal)
        self._expected_fees = defaultdict(zero_decimal)

    def on_tick(self):
        connector = self.connectors[self.exchange]

        # Always run monitoring
        self._maybe_initialize_balances(connector)
        self._log_market_snapshot(connector)
        self._log_open_orders_snapshot()
        self._log_position_snapshot(connector)

        # Test execution sequence
        if not self._passive_buy_test_completed:
            self._run_passive_buy_test(connector)
        elif self.test_sell_orders and not self._passive_sell_test_completed:
            self._run_passive_sell_test(connector)
        elif not self._market_buy_test_completed:
            self._run_market_buy_test(connector)
        elif self.test_sell_orders and not self._market_sell_test_completed:
            self._run_market_sell_test(connector)
        elif self.test_error_handling and not self._error_test_completed:
            self._run_error_handling_tests(connector)
        else:
            self._stop_if_complete()

    def did_create_buy_order(self, event: BuyOrderCreatedEvent):
        label = self._order_tags.get(event.order_id)
        if label is None:
            return

        # Record creation timestamp for performance metrics
        self._record_order_creation(event.order_id, label)

        self.log_with_clock(
            logging.INFO,
            f"YellowPro acknowledged {label} order creation: {event.order_id}.",
        )
        connector = self.connectors[self.exchange]
        self._assert_open_order_presence(event.order_id, should_exist=True, context=f"{label} create")

        if label in ["passive-buy-limit", "passive-sell-limit"]:
            self._verify_market_state_change(f"{label}-pre-order", connector, f"{label} order creation")
        elif label in ["market-buy", "market-sell"]:
            self._verify_market_state_change(f"{label}-pre-order", connector, f"{label} order creation")

        self._log_open_orders_snapshot(reason="post-creation", force=True)
        self._log_market_snapshot(connector, reason="post-creation", force=True)
        self._validate_data_consistency(connector, f"{label} creation")

    def did_create_sell_order(self, event: SellOrderCreatedEvent):
        label = self._order_tags.get(event.order_id)
        if label is None:
            return

        # Record creation timestamp for performance metrics
        self._record_order_creation(event.order_id, label)

        self.log_with_clock(
            logging.INFO,
            f"YellowPro acknowledged {label} SELL order creation: {event.order_id}.",
        )
        connector = self.connectors[self.exchange]
        self._assert_open_order_presence(event.order_id, should_exist=True, context=f"{label} create")

        if label in ["passive-sell-limit"]:
            self._verify_market_state_change(f"{label}-pre-order", connector, f"{label} order creation")
        elif label in ["market-sell"]:
            self._verify_market_state_change(f"{label}-pre-order", connector, f"{label} order creation")

        self._log_open_orders_snapshot(reason="post-creation", force=True)
        self._log_market_snapshot(connector, reason="post-creation", force=True)
        self._validate_data_consistency(connector, f"{label} creation")

    def did_fill_order(self, event: OrderFilledEvent):
        label = self._order_tags.get(event.order_id)
        if label is None:
            return

        # Record fill latency for performance metrics
        self._record_order_fill(event.order_id, label)

        self.log_with_clock(
            logging.INFO,
            (
                f"YellowPro {label} fill: {event.amount} {event.trading_pair} @ {event.price} "
                f"(fee={event.trade_fee.flat_fees})."
            ),
        )
        self._record_fill(label, event)
        connector = self.connectors[self.exchange]
        self._log_position_snapshot(connector, reason=f"{label} fill", force=True)
        self._validate_data_consistency(connector, f"{label} fill")

    def did_cancel_order(self, event: OrderCancelledEvent):
        label = self._order_tags.get(event.order_id)
        if label is None:
            return

        # Record cancel latency
        cancel_latency = self.current_timestamp - self._creation_timestamps.get(event.order_id, self.current_timestamp)
        self._performance_metrics.cancel_latency = max(self._performance_metrics.cancel_latency, cancel_latency)

        self.log_with_clock(logging.INFO, f"YellowPro cancelled {label} order: {event.order_id} (latency: {cancel_latency*1000000:.0f}μs).")

        # Update appropriate test completion flags
        if label == "passive-buy-limit":
            self._passive_buy_test_completed = True
        elif label == "passive-sell-limit":
            self._passive_sell_test_completed = True

        self._cancel_requested[event.order_id] = False
        self._creation_timestamps.pop(event.order_id, None)
        self._release_order(event.order_id)

        connector = self.connectors[self.exchange]
        self._log_open_orders_snapshot(reason="post-cancel", force=True)
        self._log_market_snapshot(connector, reason="post-cancel", force=True)
        self._log_position_snapshot(connector, reason="post-cancel", force=True)
        self._assert_open_order_presence(event.order_id, should_exist=False, context=f"{label} cancel")
        self._verify_market_state_change(f"{label}-pre-cancel", connector, f"{label} order cancel")
        self._validate_data_consistency(connector, f"{label} cancel")

    def did_complete_buy_order(self, event: BuyOrderCompletedEvent):
        label = self._order_tags.get(event.order_id)
        if label is None:
            return

        connector = self.connectors[self.exchange]
        self.log_with_clock(
            logging.INFO,
            (
                f"YellowPro reported {label} BUY completion: {event.order_id} "
                f"({event.base_asset_amount} {event.base_asset} for "
                f"{event.quote_asset_amount} {event.quote_asset})."
            ),
        )

        if label == "market-buy":
            self._market_buy_test_completed = True
            self._release_order(event.order_id)
            self._log_position_snapshot(connector, reason="market buy completion", force=True)
            self._verify_fill_expectation(label)
            self._verify_market_state_change("market-buy-pre-order", connector, "market buy order fill")
            self._verify_position_alignment(connector, reason="market buy completion")
        elif label == "passive-buy-limit" and not self._passive_buy_test_completed:
            self.log_with_clock(
                logging.WARNING,
                "Passive BUY order fully filled before cancellation; queuing another cancel attempt.",
            )
            self._release_order(event.order_id)
            self._creation_timestamps.pop(event.order_id, None)
            self._cancel_requested[event.order_id] = False

    def did_complete_sell_order(self, event: SellOrderCompletedEvent):
        label = self._order_tags.get(event.order_id)
        if label is None:
            return

        connector = self.connectors[self.exchange]
        self.log_with_clock(
            logging.INFO,
            (
                f"YellowPro reported {label} SELL completion: {event.order_id} "
                f"({event.base_asset_amount} {event.base_asset} for "
                f"{event.quote_asset_amount} {event.quote_asset})."
            ),
        )

        if label == "market-sell":
            self._market_sell_test_completed = True
            self._release_order(event.order_id)
            self._log_position_snapshot(connector, reason="market sell completion", force=True)
            self._verify_fill_expectation(label)
            self._verify_market_state_change("market-sell-pre-order", connector, "market sell order fill")
            self._verify_position_alignment(connector, reason="market sell completion")
        elif label == "passive-sell-limit" and not self._passive_sell_test_completed:
            self.log_with_clock(
                logging.WARNING,
                "Passive SELL order fully filled before cancellation; queuing another cancel attempt.",
            )
            self._release_order(event.order_id)
            self._creation_timestamps.pop(event.order_id, None)
            self._cancel_requested[event.order_id] = False

    def did_fail_order(self, event: MarketOrderFailureEvent):
        label = self._order_tags.get(event.order_id)
        if label is None:
            return

        self.log_with_clock(
            logging.ERROR,
            f"YellowPro reported failure for {label} order {event.order_id}: {event.error_message}.",
        )

        # Handle retry logic for certain errors
        if self._should_retry_order(event.error_message):
            if self._retry_counts[event.order_id] < self.order_retry_attempts:
                self._retry_counts[event.order_id] += 1
                self.log_with_clock(
                    logging.INFO,
                    f"Retrying {label} order {event.order_id} (attempt {self._retry_counts[event.order_id]}/{self.order_retry_attempts})."
                )
                return

        # Mark test as completed based on failure type
        if label == "market-buy":
            self._market_buy_test_completed = True
        elif label == "market-sell":
            self._market_sell_test_completed = True
        elif label.startswith("passive"):
            if "buy" in label:
                self._passive_buy_test_completed = True
            elif "sell" in label:
                self._passive_sell_test_completed = True

        self._creation_timestamps.pop(event.order_id, None)
        self._cancel_requested[event.order_id] = False
        self._release_order(event.order_id)

    # ==================== NEW TEST METHODS ====================

    def _run_passive_buy_test(self, connector):
        """Run passive limit BUY order test"""
        if self._passive_buy_order_id is None:
            self._submit_passive_buy_order(connector)
            return

        if (
            not self._cancel_requested.get(self._passive_buy_order_id, False)
            and self._passive_buy_order_id in self._creation_timestamps
            and self.current_timestamp - self._creation_timestamps[self._passive_buy_order_id] >= self.cancel_delay
        ):
            elapsed_time = self.current_timestamp - self._creation_timestamps[self._passive_buy_order_id]
            self.log_with_clock(
                logging.INFO,
                f"Cancelling passive BUY order {self._passive_buy_order_id} after {elapsed_time:.3f}s.",
            )
            self._capture_market_state("passive-buy-pre-cancel", connector)
            self.cancel(self.exchange, self.trading_pair, self._passive_buy_order_id)
            self._cancel_requested[self._passive_buy_order_id] = True

    def _run_passive_sell_test(self, connector):
        """Run passive limit SELL order test"""
        if self._passive_sell_order_id is None:
            self._submit_passive_sell_order(connector)
            return

        if (
            not self._cancel_requested.get(self._passive_sell_order_id, False)
            and self._passive_sell_order_id in self._creation_timestamps
            and self.current_timestamp - self._creation_timestamps[self._passive_sell_order_id] >= self.cancel_delay
        ):
            elapsed_time = self.current_timestamp - self._creation_timestamps[self._passive_sell_order_id]
            self.log_with_clock(
                logging.INFO,
                f"Cancelling passive SELL order {self._passive_sell_order_id} after {elapsed_time:.3f}s.",
            )
            self._capture_market_state("passive-sell-pre-cancel", connector)
            self.cancel(self.exchange, self.trading_pair, self._passive_sell_order_id)
            self._cancel_requested[self._passive_sell_order_id] = True

    def _run_market_buy_test(self, connector):
        """Run market BUY order test"""
        if self._market_buy_order_id is None:
            self._start_market_buy_test(connector)

    def _run_market_sell_test(self, connector):
        """Run market SELL order test"""
        if self._market_sell_order_id is None:
            self._start_market_sell_test(connector)

    def _run_error_handling_tests(self, connector):
        """Run error handling tests"""
        self._test_insufficient_balance_order(connector)
        self._test_invalid_price_order(connector)
        self._error_test_completed = True

    def _submit_passive_buy_order(self, connector):
        """Submit a passive limit BUY order"""
        try:
            mid_price = connector.get_mid_price(self.trading_pair)
            if mid_price is None:
                self.log_with_clock(logging.INFO, "Mid price unavailable yet; waiting for market data.")
                return

            # Convert to decimal first before comparison
            mid_price_decimal = self._decimal_or_none(mid_price)
            if mid_price_decimal is None or mid_price_decimal <= 0:
                self.log_with_clock(logging.INFO, f"Invalid mid price {mid_price}; waiting for valid market data.")
                return

        except Exception as e:
            self.log_with_clock(logging.WARNING, f"Error getting mid price: {e}. Retrying next tick.")
            return

        offset_multiplier = max(Decimal("0.01"), Decimal("1") - self.price_offset)
        limit_price = mid_price_decimal * offset_multiplier
        self._capture_market_state("passive-buy-pre-order", connector)

        order_id = self.buy(
            connector_name=self.exchange,
            trading_pair=self.trading_pair,
            amount=self.order_amount,
            order_type=OrderType.LIMIT,
            price=limit_price,
        )
        self._passive_buy_order_id = order_id
        self._order_tags[order_id] = "passive-buy-limit"
        self._order_start_times[order_id] = self.current_timestamp

        self.log_with_clock(
            logging.INFO,
            (
                f"Placing YellowPro passive BUY order {self.order_amount} {self.trading_pair} "
                f"@ {limit_price} (targeting cancel test)."
            ),
        )
        self._log_market_snapshot(connector, reason="pre-passive-buy-order", force=True)

    def _submit_passive_sell_order(self, connector):
        """Submit a passive limit SELL order"""
        try:
            mid_price = connector.get_mid_price(self.trading_pair)
            if mid_price is None:
                self.log_with_clock(logging.INFO, "Mid price unavailable yet; waiting for market data.")
                return

            # Convert to decimal first before comparison
            mid_price_decimal = self._decimal_or_none(mid_price)
            if mid_price_decimal is None or mid_price_decimal <= 0:
                self.log_with_clock(logging.INFO, f"Invalid mid price {mid_price}; waiting for valid market data.")
                return

        except Exception as e:
            self.log_with_clock(logging.WARNING, f"Error getting mid price: {e}. Retrying next tick.")
            return

        offset_multiplier = Decimal("1") + self.price_offset
        limit_price = mid_price_decimal * offset_multiplier
        self._capture_market_state("passive-sell-pre-order", connector)

        order_id = self.sell(
            connector_name=self.exchange,
            trading_pair=self.trading_pair,
            amount=self.order_amount,
            order_type=OrderType.LIMIT,
            price=limit_price,
        )
        self._passive_sell_order_id = order_id
        self._order_tags[order_id] = "passive-sell-limit"
        self._order_start_times[order_id] = self.current_timestamp

        self.log_with_clock(
            logging.INFO,
            (
                f"Placing YellowPro passive SELL order {self.order_amount} {self.trading_pair} "
                f"@ {limit_price} (targeting cancel test)."
            ),
        )
        self._log_market_snapshot(connector, reason="pre-passive-sell-order", force=True)

    def _start_market_buy_test(self, connector):
        """Start market BUY order test"""
        self.log_with_clock(
            logging.INFO,
            f"Placing YellowPro MARKET BUY order {self.order_amount} {self.trading_pair} for fill verification.",
        )
        self._capture_market_state("market-buy-pre-order", connector)

        order_id = self.buy(
            connector_name=self.exchange,
            trading_pair=self.trading_pair,
            amount=self.order_amount,
            order_type=OrderType.MARKET,
        )
        self._market_buy_order_id = order_id
        self._order_tags[order_id] = "market-buy"
        self._order_start_times[order_id] = self.current_timestamp

    def _start_market_sell_test(self, connector):
        """Start market SELL order test"""
        self.log_with_clock(
            logging.INFO,
            f"Placing YellowPro MARKET SELL order {self.order_amount} {self.trading_pair} for fill verification.",
        )
        self._capture_market_state("market-sell-pre-order", connector)

        order_id = self.sell(
            connector_name=self.exchange,
            trading_pair=self.trading_pair,
            amount=self.order_amount,
            order_type=OrderType.MARKET,
        )
        self._market_sell_order_id = order_id
        self._order_tags[order_id] = "market-sell"
        self._order_start_times[order_id] = self.current_timestamp

    def _maybe_initialize_balances(self, connector):
        if self._initial_balances is not None:
            return
        snapshot = self._snapshot_balances(connector)
        self._initial_balances = snapshot
        self._last_logged_balances = dict(snapshot)
        self.log_with_clock(
            logging.INFO,
            (
                f"Initial positions - {self._base_asset}: {snapshot[self._base_asset]}, "
                f"{self._quote_asset}: {snapshot[self._quote_asset]}"
            ),
        )

    def _log_position_snapshot(self, connector, reason: str | None = None, force: bool = False):
        if self._initial_balances is None:
            return
        if not force and (self.current_timestamp - self._last_balance_snapshot) < self.position_snapshot_interval:
            return

        snapshot = self._snapshot_balances(connector)
        deltas = {
            asset: snapshot[asset] - self._initial_balances.get(asset, Decimal("0"))
            for asset in snapshot
        }
        prefix = "[positions]" if reason is None else f"[positions:{reason}]"
        self.log_with_clock(
            logging.INFO,
            (
                f"{prefix} {self._base_asset}={snapshot[self._base_asset]} "
                f"(Δ={deltas[self._base_asset]}), "
                f"{self._quote_asset}={snapshot[self._quote_asset]} "
                f"(Δ={deltas[self._quote_asset]})"
            ),
        )
        self._last_logged_balances = snapshot
        self._last_balance_snapshot = self.current_timestamp

    def _log_open_orders_snapshot(self, reason: str | None = None, force: bool = False):
        if not force and (self.current_timestamp - self._last_open_orders_snapshot) < self.open_order_snapshot_interval:
            return

        try:
            orders = self.get_active_orders(self.exchange)
        except Exception:
            self.log_with_clock(logging.WARNING, "Active order snapshot unavailable.")
            return

        prefix = "[open-orders]" if reason is None else f"[open-orders:{reason}]"
        if not orders:
            summary = "No open orders."
        else:
            parts = []
            for order in orders:
                side = "BUY" if order.is_buy else "SELL"
                parts.append(f"{order.client_order_id} {side} {order.quantity} @ {order.price}")
            summary = " | ".join(parts)
        self.log_with_clock(logging.INFO, f"{prefix} {summary}")
        self._last_open_orders_snapshot = self.current_timestamp

    def _log_market_snapshot(self, connector, reason: str | None = None, force: bool = False):
        if not force and (self.current_timestamp - self._last_market_snapshot) < self.market_snapshot_interval:
            return

        try:
            state = self._fetch_market_state(connector)
            if state is None:
                self.log_with_clock(logging.INFO, f"[market:{reason or 'snapshot'}] Market state unavailable")
                return

            best_bid = state.best_bid
            best_ask = state.best_ask

            # Handle case where order book is empty
            if best_bid is None and best_ask is None:
                prefix = "[market]" if reason is None else f"[market:{reason}]"
                self.log_with_clock(logging.INFO, f"{prefix} Order book is empty - no bids or asks available")
                self._last_market_snapshot = self.current_timestamp
                return

            spread = best_ask - best_bid if best_ask is not None and best_bid is not None else None
            bid_delta = (
                best_bid - self._last_best_bid
                if best_bid is not None and self._last_best_bid is not None
                else None
            )
            ask_delta = (
                best_ask - self._last_best_ask
                if best_ask is not None and self._last_best_ask is not None
                else None
            )
            prefix = "[market]" if reason is None else f"[market:{reason}]"
            self.log_with_clock(
                logging.INFO,
                (
                    f"{prefix} best_bid={best_bid} (Δ={bid_delta}), "
                    f"best_ask={best_ask} (Δ={ask_delta}), spread={spread}"
                ),
            )
            self._last_best_bid = best_bid
            self._last_best_ask = best_ask
            self._last_market_snapshot = self.current_timestamp

        except Exception as e:
            self.log_with_clock(logging.WARNING, f"[market:{reason or 'snapshot'}] Error fetching market snapshot: {e}")
            self._last_market_snapshot = self.current_timestamp

    def _snapshot_balances(self, connector) -> Dict[str, Decimal]:
        return {
            self._base_asset: self._decimal_or_zero(connector.get_balance(self._base_asset)),
            self._quote_asset: self._decimal_or_zero(connector.get_balance(self._quote_asset)),
        }

    @staticmethod
    def _decimal_or_none(value) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @classmethod
    def _decimal_or_zero(cls, value) -> Decimal:
        decimal_value = cls._decimal_or_none(value)
        return decimal_value if decimal_value is not None else Decimal("0")

    def _release_order(self, order_id: str):
        if order_id == self._passive_buy_order_id:
            self._passive_buy_order_id = None
        if order_id == self._passive_sell_order_id:
            self._passive_sell_order_id = None
        if order_id == self._market_buy_order_id:
            self._market_buy_order_id = None
        if order_id == self._market_sell_order_id:
            self._market_sell_order_id = None
        self._order_tags.pop(order_id, None)
        self._order_start_times.pop(order_id, None)

    def _stop_if_complete(self):
        buy_tests_done = self._passive_buy_test_completed and self._market_buy_test_completed
        sell_tests_done = (not self.test_sell_orders or
                          (self._passive_sell_test_completed and self._market_sell_test_completed))
        error_tests_done = not self.test_error_handling or self._error_test_completed

        if buy_tests_done and sell_tests_done and error_tests_done:
            self.log_with_clock(logging.INFO, "YellowPro comprehensive test completed; stopping bot.")
            self._log_performance_summary()
            HummingbotApplication.main_application().stop()

    def _capture_market_state(self, label: str, connector):
        state = self._fetch_market_state(connector)
        if state is not None:
            self._market_state_markers[label] = state

    def _verify_market_state_change(self, marker_label: str, connector, reason: str):
        if marker_label not in self._market_state_markers:
            return
        previous_state = self._market_state_markers[marker_label]
        current_state = self._fetch_market_state(connector)
        if current_state is None:
            self.log_with_clock(logging.WARNING, f"[market-check:{reason}] Unable to fetch order book.")
            return
        if self._market_state_changed(previous_state, current_state, connector):
            self.log_with_clock(
                logging.INFO,
                f"[market-check:{reason}] Detected best bid/ask change "
                f"({previous_state.best_bid}->{current_state.best_bid}, "
                f"{previous_state.best_ask}->{current_state.best_ask}).",
            )
        else:
            self.log_with_clock(
                logging.WARNING,
                f"[market-check:{reason}] No change detected in best bid/ask after order action.",
            )

    def _fetch_market_state(self, connector) -> MarketState | None:
        try:
            order_book = connector.get_order_book(self.trading_pair)
            if order_book is None:
                return MarketState(best_bid=None, best_ask=None, timestamp=self.current_timestamp)

            best_bid = self._decimal_or_none(order_book.get_price(False))
            best_ask = self._decimal_or_none(order_book.get_price(True))
            return MarketState(best_bid=best_bid, best_ask=best_ask, timestamp=self.current_timestamp)
        except Exception as e:
            self.log_with_clock(logging.WARNING, f"Order book snapshot unavailable: {e}")
            return MarketState(best_bid=None, best_ask=None, timestamp=self.current_timestamp)

    def _market_state_changed(self, previous: MarketState, current: MarketState, connector) -> bool:
        rule = connector.trading_rules.get(self.trading_pair)
        min_increment = Decimal(str(rule.min_price_increment)) if rule and rule.min_price_increment is not None else Decimal("0")
        return (
            self._price_has_delta(previous.best_bid, current.best_bid, min_increment)
            or self._price_has_delta(previous.best_ask, current.best_ask, min_increment)
        )

    @staticmethod
    def _price_has_delta(prev: Decimal | None, curr: Decimal | None, min_increment: Decimal) -> bool:
        if prev is None or curr is None:
            return False
        delta = abs(curr - prev)
        threshold = max(min_increment, Decimal("0"))
        if threshold == 0:
            return delta > Decimal("0")
        return delta >= threshold

    def _assert_open_order_presence(self, order_id: str, should_exist: bool, context: str):
        try:
            orders = self.get_active_orders(self.exchange)
        except Exception:
            self.log_with_clock(logging.WARNING, f"[open-order-check:{context}] Unable to query active orders.")
            return
        found = any(order.client_order_id == order_id for order in orders)
        if found == should_exist:
            status = "present" if should_exist else "absent"
            self.log_with_clock(
                logging.INFO,
                f"[open-order-check:{context}] Expected order is {status}."
            )
        else:
            self.log_with_clock(
                logging.WARNING,
                f"[open-order-check:{context}] Unexpected active-order state (should_exist={should_exist}, found={found})."
            )

    def _record_fill(self, label: str, event: OrderFilledEvent):
        amount = self._decimal_or_zero(event.amount)
        price = self._decimal_or_zero(event.price)
        self._fill_sizes[label] += amount
        self._fill_quotes[label] += amount * price

    def _verify_fill_expectation(self, label: str):
        expected = self.order_amount if label == "market-buy" else Decimal("0")
        actual = self._fill_sizes[label]
        tolerance = max(Decimal("0.000001"), expected * self.fill_size_tolerance_pct)
        if actual + tolerance < expected:
            self.log_with_clock(
                logging.WARNING,
                f"[fill-check:{label}] Recorded fills {actual} below expected {expected} (tolerance {tolerance})."
            )
        else:
            self.log_with_clock(
                logging.INFO,
                f"[fill-check:{label}] Recorded fills {actual} match expected {expected}."
            )

    def _verify_position_alignment(self, connector, reason: str):
        if self._initial_balances is None:
            return
        snapshot = self._snapshot_balances(connector)
        base_delta = snapshot[self._base_asset] - self._initial_balances[self._base_asset]
        quote_delta = snapshot[self._quote_asset] - self._initial_balances[self._quote_asset]
        expected_base = sum(self._fill_sizes.values())
        expected_quote = -sum(self._fill_quotes.values())
        tolerance_base = max(Decimal("0.000001"), abs(expected_base) * self.fill_size_tolerance_pct)
        tolerance_quote = max(Decimal("0.0001"), abs(expected_quote) * self.fill_size_tolerance_pct)
        base_ok = abs(base_delta - expected_base) <= tolerance_base
        quote_ok = abs(quote_delta - expected_quote) <= tolerance_quote
        self.log_with_clock(
            logging.INFO if base_ok else logging.WARNING,
            f"[position-check:{reason}] Base Δ={base_delta} vs expected {expected_base} (tol {tolerance_base})."
        )
        self.log_with_clock(
            logging.INFO if quote_ok else logging.WARNING,
            f"[position-check:{reason}] Quote Δ={quote_delta} vs expected {expected_quote} (tol {tolerance_quote})."
        )

    # ==================== ENHANCED FUNCTIONALITY ====================

    def _record_order_creation(self, order_id: str, label: str):
        """Record order creation timestamp for performance metrics"""
        self._creation_timestamps[order_id] = self.current_timestamp
        if self.test_performance:
            start_time = self._order_start_times.get(order_id, self.current_timestamp)
            latency = self.current_timestamp - start_time
            self._performance_metrics.order_creation_latency[label] = latency

    def _record_order_fill(self, order_id: str, label: str):
        """Record order fill latency for performance metrics"""
        if self.test_performance and order_id in self._order_start_times:
            latency = self.current_timestamp - self._order_start_times[order_id]
            self._performance_metrics.order_fill_latency[label] = latency

    def _validate_data_consistency(self, connector, reason: str):
        """Validate data consistency across different data sources"""
        if not self.test_data_consistency:
            return

        try:
            # Check order book consistency
            order_book = connector.get_order_book(self.trading_pair)
            if order_book is None:
                self.log_with_clock(logging.WARNING, f"[consistency-check:{reason}] Order book unavailable")
                return

            best_bid = order_book.get_price(False)
            best_ask = order_book.get_price(True)

            # Validate spread
            if best_bid is not None and best_ask is not None:
                spread = best_ask - best_bid
                if spread <= 0:
                    self.log_with_clock(logging.ERROR, f"[consistency-check:{reason}] Invalid spread: {spread}")

            # Check balance consistency
            current_balances = self._snapshot_balances(connector)
            if self._last_logged_balances:
                for asset in [self._base_asset, self._quote_asset]:
                    if current_balances[asset] < 0:
                        self.log_with_clock(
                            logging.ERROR,
                            f"[consistency-check:{reason}] Negative {asset} balance: {current_balances[asset]}"
                        )

        except Exception as e:
            self.log_with_clock(logging.WARNING, f"[consistency-check:{reason}] Validation failed: {e}")

    def _test_insufficient_balance_order(self, connector):
        """Test order submission with insufficient balance"""
        try:
            # Get current balance and try to order more than available
            base_balance = connector.get_balance(self._base_asset)
            quote_balance = connector.get_balance(self._quote_asset)

            # Try to buy with insufficient quote balance
            if quote_balance is not None:
                excessive_amount = self._decimal_or_zero(quote_balance) / Decimal("0.01")  # 100x current balance
                try:
                    self.buy(
                        connector_name=self.exchange,
                        trading_pair=self.trading_pair,
                        amount=excessive_amount,
                        order_type=OrderType.MARKET,
                    )
                    self.log_with_clock(
                        logging.WARNING,
                        "[error-test] Insufficient balance order was accepted unexpectedly"
                    )
                except Exception as e:
                    self.log_with_clock(
                        logging.INFO,
                        f"[error-test] Insufficient balance correctly rejected: {e}"
                    )

        except Exception as e:
            self.log_with_clock(logging.WARNING, f"[error-test] Insufficient balance test failed: {e}")

    def _test_invalid_price_order(self, connector):
        """Test order submission with invalid price"""
        try:
            # Try to submit limit order with invalid (negative) price
            try:
                self.buy(
                    connector_name=self.exchange,
                    trading_pair=self.trading_pair,
                    amount=self.order_amount,
                    order_type=OrderType.LIMIT,
                    price=Decimal("-1"),
                )
                self.log_with_clock(
                    logging.WARNING,
                    "[error-test] Invalid price order was accepted unexpectedly"
                )
            except Exception as e:
                self.log_with_clock(
                    logging.INFO,
                    f"[error-test] Invalid price correctly rejected: {e}"
                )

        except Exception as e:
            self.log_with_clock(logging.WARNING, f"[error-test] Invalid price test failed: {e}")

    def _should_retry_order(self, error_message: str) -> bool:
        """Determine if an order should be retried based on the error message"""
        retryable_errors = [
            "network",
            "timeout",
            "connection",
            "rate limit",
            "temporarily"
        ]
        error_lower = error_message.lower()
        return any(error in error_lower for error in retryable_errors)

    def _log_performance_summary(self):
        """Log performance metrics summary"""
        if not self.test_performance:
            return

        self.log_with_clock(logging.INFO, "=== PERFORMANCE SUMMARY ===")

        # Order creation latency
        for label, latency in self._performance_metrics.order_creation_latency.items():
            self.log_with_clock(logging.INFO, f"Order creation latency - {label}: {latency*1000000:.0f}μs")

        # Order fill latency
        for label, latency in self._performance_metrics.order_fill_latency.items():
            self.log_with_clock(logging.INFO, f"Order fill latency - {label}: {latency*1000000:.0f}μs")

        # Cancel latency
        if self._performance_metrics.cancel_latency > 0:
            self.log_with_clock(
                logging.INFO,
                f"Cancel latency: {self._performance_metrics.cancel_latency*1000000:.0f}μs"
            )

        # Summary statistics
        if self._performance_metrics.order_creation_latency:
            avg_creation = sum(self._performance_metrics.order_creation_latency.values()) / len(self._performance_metrics.order_creation_latency)
            self.log_with_clock(logging.INFO, f"Average order creation latency: {avg_creation*1000000:.0f}μs")

        if self._performance_metrics.order_fill_latency:
            avg_fill = sum(self._performance_metrics.order_fill_latency.values()) / len(self._performance_metrics.order_fill_latency)
            self.log_with_clock(logging.INFO, f"Average order fill latency: {avg_fill*1000000:.0f}μs")

        self.log_with_clock(logging.INFO, "=== END PERFORMANCE SUMMARY ===")

    # Update fill expectation verification for both buy and sell
    def _verify_fill_expectation(self, label: str):
        if label in ["market-buy", "market-sell"]:
            expected = self.order_amount
        else:
            expected = Decimal("0")

        actual = self._fill_sizes[label]
        tolerance = max(Decimal("0.000001"), expected * self.fill_size_tolerance_pct)

        if actual + tolerance < expected:
            self.log_with_clock(
                logging.WARNING,
                f"[fill-check:{label}] Recorded fills {actual} below expected {expected} (tolerance {tolerance})."
            )
        else:
            self.log_with_clock(
                logging.INFO,
                f"[fill-check:{label}] Recorded fills {actual} match expected {expected}."
            )
