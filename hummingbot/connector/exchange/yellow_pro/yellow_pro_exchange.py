import asyncio
import json
import time
from collections import deque
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, AsyncIterable, Awaitable, Deque, Dict, Iterable, List, Optional, Set, Tuple

from bidict import bidict

from hummingbot.connector.constants import s_decimal_0, s_decimal_NaN
from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS, yellow_pro_web_utils as web_utils
from hummingbot.connector.exchange.yellow_pro.yellow_pro_api_order_book_data_source import YellowProAPIOrderBookDataSource
from hummingbot.connector.exchange.yellow_pro.yellow_pro_api_user_stream_data_source import YellowProAPIUserStreamDataSource
from hummingbot.connector.exchange.yellow_pro.yellow_pro_auth import YellowProAuth
from hummingbot.connector.exchange.yellow_pro.yellow_pro_order_book import YellowProOrderBook
from hummingbot.connector.exchange.yellow_pro.yellow_pro_perf_tracer import PerfTracer
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import combine_to_hb_trading_pair
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import DeductedFromReturnsTradeFee, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.utils.async_utils import safe_ensure_future, safe_gather
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory


class YellowProExchange(ExchangePyBase):
    UPDATE_ORDER_STATUS_MIN_INTERVAL = 10.0

    web_utils = web_utils

    SHORT_POLL_INTERVAL = 5.0
    LONG_POLL_INTERVAL = 120.0
    ORDER_SNAPSHOT_CACHE_TTL = SHORT_POLL_INTERVAL
    TRADE_SNAPSHOT_CACHE_TTL = SHORT_POLL_INTERVAL
    MAX_TRADE_HISTORY_PAGES = 5
    MAX_ORDER_HISTORY_PAGES = 5
    MAX_OPEN_ORDERS_PAGES = 1
    OPEN_ORDERS_RECONCILIATION_INTERVAL = 5.0

    def __init__(
            self,
            yellow_pro_app_session_id: str,
            yellow_pro_api_key: str = "",
            yellow_pro_api_secret: str = "",
            yellow_pro_channel_id: Optional[str] = None,
            yellow_pro_leverage: Optional[Decimal] = Decimal("1"),
            balance_asset_limit: Optional[Dict[str, Dict[str, Decimal]]] = None,
            rate_limits_share_pct: Decimal = Decimal("100"),
            trading_pairs: Optional[List[str]] = None,
            trading_required: bool = True,
            yellow_pro_domain: str = CONSTANTS.DOMAIN,
            open_orders_reconciliation_interval: float = OPEN_ORDERS_RECONCILIATION_INTERVAL):
        self._domain = yellow_pro_domain if yellow_pro_domain in CONSTANTS.REST_URLS else CONSTANTS.DOMAIN
        self._app_session_id = yellow_pro_app_session_id
        if not self._app_session_id:
            raise ValueError("YellowPro app session id is required.")
        channel_id = (yellow_pro_channel_id or "").strip()
        self._channel_id = channel_id or None
        leverage_value = yellow_pro_leverage
        if leverage_value is not None:
            self._default_leverage = self._decimal_to_str(leverage_value)
        else:
            self._default_leverage = None
        self._trading_pairs = list(trading_pairs) if trading_pairs is not None else []
        self._trading_required = trading_required
        self._auth_instance = YellowProAuth(
            api_key=yellow_pro_api_key,
            api_secret=yellow_pro_api_secret,
            domain=self._domain)
        self._trading_pair_symbol_map = bidict()
        self._processed_trade_ids: Deque[str] = deque()
        self._processed_trade_ids_lookup: Set[str] = set()
        self._processed_trade_ids_limit = 1000
        self._order_numeric_ids: Dict[str, str] = {}
        self._orders_cache: Dict[str, Dict[str, Any]] = {}
        self._trades_cache: Optional[Dict[str, Any]] = None
        self.real_time_balance_update = False
        self._last_ready_status: Dict[str, bool] = {}
        self._symbol_map_refresh_task: Optional[asyncio.Task] = None
        self._trading_rules_refresh_task: Optional[asyncio.Task] = None
        self._balance_refresh_task: Optional[asyncio.Task] = None
        self._open_orders_reconciliation_task: Optional[asyncio.Task] = None
        # Track cancel attempts for untracked orders to avoid infinite retry
        self._cancel_attempts_map: Dict[str, int] = {}
        try:
            interval = float(open_orders_reconciliation_interval)
            self._open_orders_reconciliation_interval = interval if interval > 0 else self.OPEN_ORDERS_RECONCILIATION_INTERVAL
        except Exception:
            self._open_orders_reconciliation_interval = self.OPEN_ORDERS_RECONCILIATION_INTERVAL
        self._startup_timestamp = time.time()
        self._perf_tracer = PerfTracer()
        super().__init__(balance_asset_limit, rate_limits_share_pct)

    @property
    def name(self) -> str:
        return CONSTANTS.EXCHANGE_NAME

    @property
    def authenticator(self) -> Optional[YellowProAuth]:
        return self._auth_instance

    @property
    def rate_limits_rules(self) -> List[RateLimit]:
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def client_order_id_max_length(self) -> int:
        return CONSTANTS.MAX_ORDER_ID_LEN

    @property
    def client_order_id_prefix(self) -> str:
        return CONSTANTS.BROKER_ID

    @property
    def trading_rules_request_path(self) -> str:
        return CONSTANTS.EXCHANGE_INFO_URL

    @property
    def trading_pairs_request_path(self) -> str:
        return CONSTANTS.EXCHANGE_INFO_URL

    @property
    def check_network_request_path(self) -> str:
        return CONSTANTS.CHECK_NETWORK_URL

    @property
    def trading_pairs(self) -> Optional[List[str]]:
        return self._trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return True

    @property
    def is_trading_required(self) -> bool:
        return self._trading_required

    @property
    def status_dict(self) -> Dict[str, bool]:
        status = super().status_dict
        # Add open orders reconciliation as a ready condition
        if self.is_trading_required:
            reconciliation_ready = (
                self._open_orders_reconciliation_task is not None
                and not self._open_orders_reconciliation_task.done()
            )
            status["open_orders_reconciliation_initialized"] = reconciliation_ready
        if status == self._last_ready_status:
            return status
        for key, value in status.items():
            if self._last_ready_status.get(key) != value:
                log_level = self.logger().info if value else self.logger().warning
                log_level("YellowPro ready check %s=%s", key, value)
        self._last_ready_status = dict(status)
        pending_keys = [key for key, ready in status.items() if not ready]
        if pending_keys:
            self._trigger_ready_recovery(pending_keys)
        return status

    def _schedule_ready_task(self, attr_name: str, coroutine: Awaitable[Any]):
        existing_task: Optional[asyncio.Task] = getattr(self, attr_name, None)
        if existing_task is not None and not existing_task.done():
            return
        task = safe_ensure_future(coroutine)
        setattr(self, attr_name, task)
        task.add_done_callback(lambda _: setattr(self, attr_name, None))

    def _trigger_ready_recovery(self, pending_keys: Iterable[str]):
        if "symbols_mapping_initialized" in pending_keys:
            self._schedule_ready_task("_symbol_map_refresh_task", self._initialize_trading_pair_symbol_map())
        if "trading_rule_initialized" in pending_keys and self.is_trading_required:
            self._schedule_ready_task("_trading_rules_refresh_task", self._update_trading_rules())
        if "account_balance" in pending_keys and self.is_trading_required:
            self._schedule_ready_task("_balance_refresh_task", self._update_balances())
        if "order_books_initialized" in pending_keys:
            stream_listener_task = getattr(self.order_book_tracker, "_order_book_stream_listener_task", None)
            if stream_listener_task is None or stream_listener_task.done():
                self.order_book_tracker.start()
        if "user_stream_initialized" in pending_keys and self.is_trading_required:
            if self._user_stream_tracker_task is None or self._user_stream_tracker_task.done():
                self._user_stream_tracker_task = self._create_user_stream_tracker_task()
                self._user_stream_tracker_task.add_done_callback(
                    lambda _: setattr(self, "_user_stream_tracker_task", None))
            if self._user_stream_event_listener_task is None or self._user_stream_event_listener_task.done():
                self._user_stream_event_listener_task = safe_ensure_future(self._user_stream_event_listener())
                self._user_stream_event_listener_task.add_done_callback(
                    lambda _: setattr(self, "_user_stream_event_listener_task", None))
        if "open_orders_reconciliation_initialized" in pending_keys and self.is_trading_required:
            self._start_open_orders_reconciliation()
        if not self._poll_notifier.is_set():
            self._poll_notifier.set()

    async def _make_network_check_request(self):
        await self._api_get(path_url=self.check_network_request_path, is_auth_required=True)

    async def start_network(self):
        await super().start_network()

    async def stop_network(self):
        await self._stop_open_orders_reconciliation()
        await self._cancel_all_open_orders()
        await super().stop_network()
        await self._perf_tracer.stop()

    def supported_order_types(self) -> List[OrderType]:
        return [OrderType.LIMIT, OrderType.LIMIT_MAKER, OrderType.MARKET]

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception):
        return False

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        return web_utils.build_api_factory(throttler=self._throttler, auth=self._auth)

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        data_source = YellowProAPIOrderBookDataSource(
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self.domain,
        )
        data_source.order_book_create_function = lambda: YellowProOrderBook()
        return data_source

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        return YellowProAPIUserStreamDataSource(
            auth=self._auth_instance,
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self.domain,
        )

    async def _make_trading_rules_request(self) -> Dict[str, Any]:
        return await self._api_get(path_url=self.trading_rules_request_path, is_auth_required=True)

    async def _make_trading_pairs_request(self) -> Dict[str, Any]:
        try:
            response = await self._api_get(
                path_url=self.trading_pairs_request_path,
                params=None,
                is_auth_required=True,
                limit_id=self.trading_pairs_request_path,
            )
        except Exception as request_err:
            self.logger().warning(
                "YellowPro failed to fetch trading pairs from %s: %s",
                self.trading_pairs_request_path,
                request_err,
            )
            raise

        if not response:
            self.logger().warning(
                "YellowPro trading pairs endpoint returned an empty payload. Defaulting to no symbols."
            )
            return {"symbols": []}

        if isinstance(response, dict):
            return response

        raise IOError(f"Unexpected response type from YellowPro trading pairs endpoint: {type(response)}")

    async def _format_trading_rules(self, exchange_info: Dict[str, Any]) -> List[TradingRule]:
        rules = []
        symbols = exchange_info.get("symbols", [])
        for symbol_info in filter(web_utils.is_exchange_information_valid, symbols):
            base = symbol_info.get("base_asset")
            quote = symbol_info.get("quote_asset")
            if base is None or quote is None:
                continue
            trading_pair = combine_to_hb_trading_pair(base.upper(), quote.upper())
            base_precision = Decimal(10) ** -Decimal(symbol_info.get("base_asset_precision", 8))
            quote_precision = Decimal(10) ** -Decimal(symbol_info.get("quote_asset_precision", 8))
            min_order_size = max(base_precision, Decimal(str(symbol_info.get("min_quantity", base_precision))))
            min_price_increment = quote_precision
            rules.append(
                TradingRule(
                    trading_pair=trading_pair,
                    min_order_size=min_order_size,
                    min_price_increment=min_price_increment,
                    min_base_amount_increment=base_precision,
                    min_notional_size=s_decimal_0,
                )
            )
        return rules

    async def _initialize_trading_pair_symbol_map(self):
        exchange_info = await self._make_trading_pairs_request()
        self._initialize_trading_pair_symbols_from_exchange_info(exchange_info)

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        mapping = bidict()
        for symbol_info in filter(web_utils.is_exchange_information_valid, exchange_info.get("symbols", [])):
            exchange_symbol = symbol_info.get("symbol")
            base = symbol_info.get("base_asset")
            quote = symbol_info.get("quote_asset")
            if exchange_symbol is None or base is None or quote is None:
                continue
            trading_pair = combine_to_hb_trading_pair(base.upper(), quote.upper())
            mapping[exchange_symbol.upper()] = trading_pair
        self._set_trading_pair_symbol_map(mapping)
        if len(mapping) == 0:
            self.logger().warning(
                "YellowPro trading pair mapping is empty. Ensure the target domain exposes spot symbols."
            )
        else:
            self.logger().info(
                "YellowPro trading pair mapping initialized with symbols: %s",
                ", ".join(sorted(mapping.values())),
            )

    async def get_all_pairs_prices(self) -> List[Dict[str, str]]:
        prices = []
        if not self._trading_pairs:
            return prices
        tasks = []
        symbols: List[str] = []
        for trading_pair in self._trading_pairs:
            symbol = await self.exchange_symbol_associated_to_pair(trading_pair)
            symbols.append(symbol)
            tasks.append(self._api_get(
                path_url=CONSTANTS.TICKER_PRICE_CHANGE_URL,
                params={"symbol": symbol},
                limit_id=CONSTANTS.TICKER_PRICE_CHANGE_URL,
                is_auth_required=True))
        responses = await safe_gather(*tasks, return_exceptions=True)
        for symbol, response in zip(symbols, responses):
            if isinstance(response, Exception):
                continue
            price = response.get("last")
            if price is not None:
                prices.append({"symbol": symbol, "price": price})
        return prices

    async def get_last_traded_prices(self, trading_pairs: Optional[List[str]] = None) -> Dict[str, float]:
        trading_pairs = trading_pairs or self._trading_pairs or []
        results: Dict[str, float] = {}
        if not trading_pairs:
            return results
        symbols: List[Tuple[str, str]] = []
        tasks = []
        for trading_pair in trading_pairs:
            try:
                symbol = await self.exchange_symbol_associated_to_pair(trading_pair)
            except Exception:
                self.logger().debug(f"Trading pair {trading_pair} is not mapped to a YellowPro market.", exc_info=True)
                continue
            symbols.append((trading_pair, symbol))
            tasks.append(
                self._api_get(
                    path_url=CONSTANTS.TICKER_PRICE_CHANGE_URL,
                    params={"symbol": symbol},
                    limit_id=CONSTANTS.TICKER_PRICE_CHANGE_URL,
                    is_auth_required=True,
                )
            )
        responses = await safe_gather(*tasks, return_exceptions=True)
        for (trading_pair, _), response in zip(symbols, responses):
            if isinstance(response, Exception):
                self.logger().debug(f"Failed to fetch ticker for {trading_pair}: {response}")
                continue
            last_price = response.get("last") or response.get("lastPrice")
            if last_price is not None:
                try:
                    results[trading_pair] = float(last_price)
                except (TypeError, ValueError):
                    self.logger().debug(f"Unexpected ticker payload for {trading_pair}: {response}")
        return results

    async def get_positions(self, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieve open positions for the provided (or configured) channel.
        """
        effective_channel_id = (channel_id or self._channel_id or "").strip()
        if not effective_channel_id:
            raise ValueError("YellowPro channel id is required to fetch positions.")
        params = {"channel_id": effective_channel_id}
        response = await self._api_get(
            path_url=CONSTANTS.POSITIONS_URL,
            params=params,
            is_auth_required=True,
            limit_id=CONSTANTS.POSITIONS_URL)
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            positions_payload = response.get("positions")
            if isinstance(positions_payload, list):
                return positions_payload
        if response:
            self.logger().debug("Unexpected YellowPro positions payload: %s", response)
        return []

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        return CONSTANTS.ORDER_NOT_EXIST_MESSAGE in str(status_update_exception)

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        return CONSTANTS.UNKNOWN_ORDER_MESSAGE in str(cancelation_exception)

    def quantize_order_price(self, trading_pair: str, price: Decimal) -> Decimal:
        trading_rule = self._trading_rules.get(trading_pair)
        if trading_rule is None or price.is_nan():
            return price
        increment = trading_rule.min_price_increment
        if increment and increment > Decimal("0"):
            try:
                price = price.quantize(increment, rounding=ROUND_DOWN)
            except (ValueError, InvalidOperation):
                price = (price // increment) * increment
        return price

    def quantize_order_amount(self, trading_pair: str, amount: Decimal) -> Decimal:
        trading_rule = self._trading_rules.get(trading_pair)
        if trading_rule is None or amount.is_nan():
            return amount
        step_size = trading_rule.min_base_amount_increment
        if step_size and step_size > Decimal("0"):
            try:
                amount = amount.quantize(step_size, rounding=ROUND_DOWN)
            except (ValueError, InvalidOperation):
                amount = (amount // step_size) * step_size
        return amount

    async def _update_balances(self):
        params = {"app_session_id": self._app_session_id}
        account_info = await self._api_get(
            path_url=CONSTANTS.ACCOUNT_INFO_URL,
            params=params,
            is_auth_required=True,
            limit_id=CONSTANTS.ACCOUNT_INFO_URL)
        self.logger().debug(f"YellowPro account info response: {account_info}")
        balances_raw = account_info.get("balances", {})
        if isinstance(balances_raw, dict):
            balance_iterable = balances_raw.items()
        elif isinstance(balances_raw, list):
            balance_iterable = (
                (entry.get("asset_symbol"), entry)
                for entry in balances_raw
                if isinstance(entry, dict) and entry.get("asset_symbol") is not None
            )
        else:
            balance_iterable = []
            self.logger().debug(f"YellowPro unexpected balances payload type: {type(balances_raw)}")
        remote_assets = set()
        for asset_symbol, balance_info in balance_iterable:
            if asset_symbol is None:
                continue
            asset_symbol = str(asset_symbol)
            available = Decimal(balance_info.get("available_balance", "0"))
            total = Decimal(balance_info.get("total_balance", "0"))
            self._account_available_balances[asset_symbol] = available
            self._account_balances[asset_symbol] = total
            remote_assets.add(asset_symbol)
        local_assets = set(self._account_balances.keys())
        for asset in local_assets.difference(remote_assets):
            self._account_balances.pop(asset, None)
            self._account_available_balances.pop(asset, None)

    def _decimal_to_str(self, value: Decimal) -> str:
        return f"{value:.15f}".rstrip("0").rstrip(".")

    @staticmethod
    def _normalize_bool(value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        if isinstance(value, (int, float, Decimal)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "t", "yes", "y", "maker"}:
                return True
            if normalized in {"false", "0", "f", "no", "n", "taker"}:
                return False
        return None

    def _is_maker_from_payload(self, payload: Dict[str, Any]) -> bool:
        for key in ("is_maker", "maker"):
            normalized = self._normalize_bool(payload.get(key))
            if normalized is not None:
                return normalized
        maker_id = payload.get("maker_id")
        channel_reference = (self._channel_id or self._app_session_id or "").strip()
        if maker_id is not None and channel_reference:
            return str(maker_id).strip().lower() == channel_reference.lower()
        return False

    def _orders_cache_key(self, market: str) -> str:
        channel_reference = (self._channel_id or self._app_session_id or "").strip().lower()
        return f"{market.upper()}:{channel_reference}"

    @staticmethod
    def _find_order_in_snapshot(orders: Iterable[Dict[str, Any]], exchange_order_id: str) -> Optional[Dict[str, Any]]:
        for order in orders:
            if str(order.get("order_id")) == exchange_order_id:
                return order
        return None

    def _validate_yellow_pro_response(
            self,
            payload: Any,
            operation: str,
            required_fields: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise IOError(f"YellowPro {operation} unexpected response type: {payload}")
        error_code = payload.get("error")
        if error_code:
            message = payload.get("message") or payload.get("error_description") or ""
            raise IOError(f"YellowPro {operation} failed: {error_code} {message}".strip())
        success_flag = payload.get("success")
        normalized_success = self._normalize_bool(success_flag)
        if normalized_success is False:
            raise IOError(f"YellowPro {operation} failed: success flag set to false. Payload: {payload}")
        status_value = payload.get("status")
        if isinstance(status_value, str) and status_value.strip().lower() in {"error", "failed", "failure"}:
            raise IOError(f"YellowPro {operation} failed: status={status_value}. Payload: {payload}")
        for field in required_fields or []:
            value = payload.get(field)
            if value is None or (isinstance(value, str) and value.strip() == ""):
                raise IOError(f"YellowPro {operation} missing required field '{field}'. Payload: {payload}")
        return payload

    async def _download_orders_snapshot(
            self,
            market: str,
            force_refresh: bool = False,
            target_order_id: Optional[str] = None) -> List[Dict[str, Any]]:
        key = self._orders_cache_key(market)
        cached = self._orders_cache.get(key)
        now = self.current_timestamp
        if not force_refresh and cached is not None:
            if now - cached.get("timestamp", 0) <= self.ORDER_SNAPSHOT_CACHE_TTL:
                return list(cached.get("orders", []))
        page_size = 100
        params = {
            "app_session_id": self._app_session_id,
            "market": market,
            "page_size": page_size,
        }
        channel_id = self._channel_id or self._app_session_id
        if channel_id:
            params["channel_id"] = channel_id
        aggregated: List[Dict[str, Any]] = []
        for page in range(1, self.MAX_ORDER_HISTORY_PAGES + 1):
            params["page"] = page
            response = await self._api_get(
                path_url=CONSTANTS.ORDERS_URL,
                params=params,
                is_auth_required=True,
                limit_id=CONSTANTS.ORDERS_URL,
            )
            orders = response.get("orders", [])
            if not isinstance(orders, list):
                orders = []
            aggregated.extend(orders)
            page_contains_target = False
            if target_order_id is not None:
                page_contains_target = (
                    self._find_order_in_snapshot(orders, target_order_id) is not None
                )
            if not orders or len(orders) < page_size or page_contains_target:
                break
        self._orders_cache[key] = {
            "timestamp": now,
            "orders": aggregated,
        }
        return list(aggregated)

    async def _fetch_open_orders_for_market(self, market: Optional[str] = None) -> List[Dict[str, Any]]:
        page_size = 100
        params = {
            "app_session_id": self._app_session_id,
            "page_size": page_size,
        }
        if market is not None:
            normalized_market = str(market).upper()
            params["market"] = normalized_market
        else:
            normalized_market = "ALL"
        channel_id = (self._channel_id or self._app_session_id or "").strip()
        if channel_id:
            params["channel_id"] = channel_id
        aggregated: List[Dict[str, Any]] = []
        for page in range(1, self.MAX_OPEN_ORDERS_PAGES + 1):
            params["page"] = page
            try:
                response = await self._api_get(
                    path_url=CONSTANTS.OPEN_ORDERS_URL,
                    params=params,
                    is_auth_required=True,
                    limit_id=CONSTANTS.OPEN_ORDERS_URL,
                )
            except asyncio.CancelledError:
                raise
            except Exception as request_err:
                self.logger().warning(
                    "YellowPro open orders request failed for %s page %s: %s",
                    normalized_market,
                    page,
                    request_err,
                    exc_info=True,
                )
                if not aggregated:
                    try:
                        fallback_orders = await self._download_orders_snapshot(
                            normalized_market,
                            force_refresh=True,
                        )
                        aggregated.extend(fallback_orders)
                    except Exception:
                        self.logger().warning(
                            "YellowPro open orders fallback to orders snapshot failed for %s.",
                            normalized_market,
                            exc_info=True,
                        )
                break
            orders = response.get("orders")
            if not isinstance(orders, list):
                orders = response.get("open_orders")
            if not isinstance(orders, list):
                orders = []
                if response:
                    self.logger().debug(
                        "Unexpected YellowPro open orders payload for %s: %s",
                        normalized_market,
                        response,
                    )
            aggregated.extend(orders)
            if not orders or len(orders) < page_size:
                break
        return aggregated

    async def _cancel_order_by_uuid(self, market: str, order_uuid: str, client_order_id: Optional[str] = None) -> bool:
        if order_uuid is None:
            return False

        # Record cancel request time
        if client_order_id:
            safe_ensure_future(self._perf_tracer.record_event(
                order_id=client_order_id,
                event_type="cancel_request_time",
                timestamp=time.time()
            ))

        normalized_market = str(market).upper()
        # Look up tracked order to get order type for cancel payload
        tracked = self._order_tracker.all_fillable_orders_by_exchange_order_id.get(order_uuid)
        if tracked is None:
            tracked = self._order_tracker.all_updatable_orders_by_exchange_order_id.get(order_uuid)
        if tracked is None:
            order_type_str = "limit"
        elif tracked.order_type.is_limit_type():
            order_type_str = "limit"
        else:
            order_type_str = "market"
        payload = {
            "app_session_id": self._app_session_id,
            "channelID": self._app_session_id,
            "market": normalized_market,
            "order_uuid": order_uuid,
            "type": order_type_str,
        }
        response = await self._api_delete(
            path_url=CONSTANTS.CANCEL_ORDER_URL,
            data=payload,
            is_auth_required=True,
            limit_id=CONSTANTS.CANCEL_ORDER_URL,
        )
        validated_response = self._validate_yellow_pro_response(
            payload=response,
            operation="order cancellation",
            required_fields=("message",),
        )
        if not isinstance(validated_response.get("message"), str):
            self.logger().debug("YellowPro cancel response missing message field: %s", validated_response)

        # Record cancel ack
        if client_order_id:
            safe_ensure_future(self._perf_tracer.record_event(
                order_id=client_order_id,
                event_type="cancel_ack_time",
                timestamp=time.time()
            ))
        return True

    async def _cancel_open_orders_for_market(self, market: str) -> int:
        open_orders = await self._fetch_open_orders_for_market(market)
        canceled = 0
        for order in open_orders:
            order_uuid = order.get("order_uuid") or order.get("uuid") or order.get("order_id")
            if order_uuid is None:
                continue
            try:
                if await self._cancel_order_by_uuid(market, str(order_uuid)):
                    canceled += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().warning(
                    "Failed to cancel YellowPro open order %s on market %s.",
                    order_uuid,
                    market,
                    exc_info=True,
                )
        if canceled > 0:
            self.logger().info("YellowPro canceled %s open orders on market %s.", canceled, market)
        else:
            self.logger().debug("YellowPro open order cleanup found no orders to cancel on market %s.", market)
        return canceled

    async def _cancel_all_open_orders(self):
        if not self.is_trading_required:
            return

        open_orders = await self._fetch_open_orders_for_market()
        if not open_orders:
            self.logger().debug("YellowPro open order cleanup found no orders to cancel.")
            return

        total_canceled = 0
        for order in open_orders:
            order_uuid = order.get("order_uuid") or order.get("uuid") or order.get("order_id")
            if order_uuid is None:
                continue

            market = order.get("market")
            if market is None:
                self.logger().warning(
                    "YellowPro cannot cancel order %s: market field missing.",
                    order_uuid
                )
                continue

            try:
                if await self._cancel_order_by_uuid(market, str(order_uuid)):
                    total_canceled += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().warning(
                    "Failed to cancel YellowPro open order %s on market %s.",
                    order_uuid,
                    market,
                    exc_info=True,
                )

        if total_canceled > 0:
            self.logger().info(
                "YellowPro open order cleanup canceled %s orders.",
                total_canceled,
            )
        else:
            self.logger().debug("YellowPro open order cleanup found no orders to cancel.")

    def _start_open_orders_reconciliation(self):
        if self._open_orders_reconciliation_task is None or self._open_orders_reconciliation_task.done():
            self._open_orders_reconciliation_task = safe_ensure_future(self._open_orders_reconciliation_loop())
            self._open_orders_reconciliation_task.add_done_callback(
                lambda _: setattr(self, "_open_orders_reconciliation_task", None))
        self._perf_tracer.start()

    async def _stop_open_orders_reconciliation(self):
        await self._reconcile_open_orders()

        task = self._open_orders_reconciliation_task
        if task is not None:
            task.cancel()
            self._open_orders_reconciliation_task = None
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                self.logger().debug("YellowPro open orders reconciliation task stopped with error.", exc_info=True)

    async def _open_orders_reconciliation_loop(self):
        while True:
            try:
                await self._reconcile_open_orders()
                await self._sleep(self._open_orders_reconciliation_interval)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("YellowPro open orders reconciliation loop failed.")
                await self._sleep(1.0)

    async def _reconcile_open_orders(self):
        if not self.is_trading_required:
            return

        # Fetch all open orders across all markets in one call
        open_orders = await self._fetch_open_orders_for_market()

        # Statistics
        fetched_count = len(open_orders)
        found_in_active = 0
        not_found_in_active = 0
        filtered_by_time = 0

        for open_order in open_orders:
            exchange_order_id = (
                open_order.get("order_uuid")
                or open_order.get("uuid")
                or open_order.get("order_id")
            )
            if exchange_order_id is None:
                continue
            exchange_order_id = str(exchange_order_id)

            # Filter by created_at: only process orders created after startup
            created_at = open_order.get("created_at")
            if created_at:
                created_timestamp = self._parse_iso_timestamp(created_at)
                if created_timestamp > 0 and created_timestamp < self._startup_timestamp:
                    # Order was created before this session started, skip it
                    filtered_by_time += 1
                    continue

            # Check if this order is tracked in active orders
            if any(
                order.exchange_order_id == exchange_order_id
                for order in self._order_tracker.active_orders.values()
                if order.exchange_order_id
            ):
                found_in_active += 1
                continue

            # Order not tracked - cancel it
            not_found_in_active += 1
            market = open_order.get("market")
            if market is None:
                self.logger().warning(
                    "YellowPro cannot cancel untracked open order %s: market field missing.",
                    exchange_order_id
                )
                continue

            # Check cancel attempts, skip if exceeded max retries
            cancel_attempts = self._cancel_attempts_map.get(exchange_order_id, 0)
            if cancel_attempts >= 3:
                self.logger().debug(
                    "YellowPro skipping cancel for order %s on market %s: already attempted %s times.",
                    exchange_order_id,
                    market,
                    cancel_attempts
                )
                not_found_in_active -= 1
                continue

            # Increment cancel attempt counter
            self._cancel_attempts_map[exchange_order_id] = cancel_attempts + 1

            try:
                await self._cancel_order_by_uuid(market, exchange_order_id)
                self.logger().warning(
                    "YellowPro canceled untracked open order %s on market %s (attempt %s).",
                    exchange_order_id,
                    market,
                    self._cancel_attempts_map[exchange_order_id]
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().warning(
                    "YellowPro failed to cancel untracked open order %s on market %s (attempt %s).",
                    exchange_order_id,
                    market,
                    self._cancel_attempts_map[exchange_order_id],
                    exc_info=True,
                )

        # Log statistics
        self.logger().info(
            "YellowPro order reconciliation - "
            "Fetched from exchange: %s orders, "
            "Filtered by created_at (before startup): %s, "
            "Found in active orders: %s, "
            "Not found in active (canceled): %s",
            fetched_count,
            filtered_by_time,
            found_in_active,
            not_found_in_active
        )

    async def _download_trades_snapshot(
            self,
            force_refresh: bool = False,
            stop_when_no_new: bool = True) -> List[Dict[str, Any]]:
        cache = self._trades_cache
        now = self.current_timestamp
        if not force_refresh and cache is not None:
            if now - cache.get("timestamp", 0) <= self.TRADE_SNAPSHOT_CACHE_TTL:
                return list(cache.get("trades", []))
        page_size = 100
        params = {
            "app_session_id": self._app_session_id,
            "page_size": page_size,
        }
        channel_id = self._channel_id or self._app_session_id
        if channel_id:
            params["channel_id"] = channel_id
        aggregated: List[Dict[str, Any]] = []
        for page in range(1, self.MAX_TRADE_HISTORY_PAGES + 1):
            params["page"] = page
            trades_response = await self._api_get(
                path_url=CONSTANTS.TRADES_URL,
                params=params,
                is_auth_required=True,
                limit_id=CONSTANTS.TRADES_URL,
            )
            trades = trades_response.get("trades", [])
            if not isinstance(trades, list):
                trades = []
            aggregated.extend(trades)
            page_has_new = any(str(trade.get("id")) not in self._processed_trade_ids_lookup for trade in trades)
            if not trades or len(trades) < page_size:
                break
            if stop_when_no_new and not page_has_new:
                break
        self._trades_cache = {
            "timestamp": now,
            "trades": aggregated,
        }
        return list(aggregated)

    def _trade_update_from_record(
            self,
            trade: Dict[str, Any],
            order: InFlightOrder,
            numeric_id: Optional[str]) -> Optional[TradeUpdate]:
        order_uuid = trade.get("order_uuid")
        if order_uuid is not None:
            if str(order_uuid) != order.exchange_order_id:
                return None
        elif numeric_id is None or str(trade.get("order_id")) != numeric_id:
            return None
        amount = Decimal(trade.get("amount", "0"))
        price = Decimal(trade.get("price", "0"))
        is_maker = self._is_maker_from_payload(trade)
        fee = TradeFeeBase.new_spot_fee(
            fee_schema=self.trade_fee_schema(),
            trade_type=order.trade_type,
            percent=self.estimate_fee_pct(is_maker),
        )
        return TradeUpdate(
            trade_id=str(trade.get("id")),
            client_order_id=order.client_order_id,
            exchange_order_id=order.exchange_order_id,
            trading_pair=order.trading_pair,
            fill_base_amount=amount,
            fill_quote_amount=amount * price,
            fill_price=price,
            fee=fee,
            fill_timestamp=self._parse_iso_timestamp(trade.get("executed_at")),
        )

    async def _place_order(
            self,
            order_id: str,
            trading_pair: str,
            amount: Decimal,
            trade_type: TradeType,
            order_type: OrderType,
            price: Decimal,
            **kwargs) -> Tuple[str, float]:
        exchange_symbol = await self.exchange_symbol_associated_to_pair(trading_pair)
        order_side = "buy" if trade_type is TradeType.BUY else "sell"
        normalized_market = str(exchange_symbol).upper()
        order_payload = {
            "app_session_id": self._app_session_id,
            "market": normalized_market,
            "side": order_side,
            "amount": self._decimal_to_str(amount),
        }
        channel_override = kwargs.get("channel_id")
        channel_id = (str(channel_override).strip()
                      if channel_override is not None
                      else (self._channel_id or self._app_session_id))
        if channel_id:
            order_payload["channelID"] = channel_id
        leverage_override = kwargs.get("leverage")
        leverage_value: Optional[str]
        if leverage_override is not None:
            leverage_value = str(leverage_override)
        else:
            leverage_value = self._default_leverage
        if leverage_value:
            order_payload["leverage"] = leverage_value
        if order_type is OrderType.MARKET:
            order_payload["type"] = "market"
            order_payload["time_in_force"] = "ioc"
        else:
            tif = "gtc"
            if order_type is OrderType.LIMIT_MAKER:
                tif = "gtc"
            order_payload.update({
                "type": "limit",
                "price": self._decimal_to_str(price),
                "time_in_force": tif,
            })
        # Capture request time
        request_time = time.time()

        # Use execute_request_and_get_response to access headers
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        url = await self._api_request_url(path_url=CONSTANTS.CREATE_ORDER_URL, is_auth_required=True)

        response = await rest_assistant.execute_request_and_get_response(
            url=url,
            data=order_payload,
            method=RESTMethod.POST,
            is_auth_required=True,
            throttler_limit_id=CONSTANTS.CREATE_ORDER_URL,
        )
        # Extract X-Trace-Id and record request with trace_id
        headers = response.headers
        trace_id = headers.get("X-Trace-Id")
        safe_ensure_future(self._perf_tracer.record_event(
            order_id=order_id,
            event_type="request_time",
            timestamp=request_time,
            trace_id=trace_id
        ))

        response_json = await response.json()

        # Check if response contains order_uuid before validation (from HEAD)
        if not isinstance(response_json, dict) or not response_json.get("order_uuid"):
            self.logger().error(
                "YellowPro order request for %s failed: missing order_uuid in response. Payload: %s",
                order_id,
                response_json,
            )

        self.logger().info(
            "YellowPro order request for %s responded with payload: %s",
            order_id,
            response_json,
        )

        validated_response = self._validate_yellow_pro_response(
            payload=response_json,
            operation="order placement",
            required_fields=("order_uuid",),
        )
        exchange_order_id = str(validated_response["order_uuid"])

        # Record order_uuid and ack_time
        safe_ensure_future(self._perf_tracer.record_event(
            order_id=order_id,
            event_type="ack_time",
            timestamp=time.time(),
            order_uuid=exchange_order_id
        ))

        return exchange_order_id, self.current_timestamp

    async def _place_order_and_process_update(self, order: InFlightOrder, **kwargs) -> str:
        exchange_order_id, update_timestamp = await self._place_order(
            order_id=order.client_order_id,
            trading_pair=order.trading_pair,
            amount=order.amount,
            trade_type=order.trade_type,
            order_type=order.order_type,
            price=order.price,
            **kwargs,
        )

        exchange_order_id_str = str(exchange_order_id)
        if order.exchange_order_id is None:
            order.update_exchange_order_id(exchange_order_id_str)
        elif order.exchange_order_id != exchange_order_id_str:
            self.logger().warning(
                "Exchange order id mismatch for %s: tracked=%s, received=%s",
                order.client_order_id,
                order.exchange_order_id,
                exchange_order_id_str,
            )

        order_update: OrderUpdate = OrderUpdate(
            client_order_id=order.client_order_id,
            exchange_order_id=exchange_order_id_str,
            trading_pair=order.trading_pair,
            update_timestamp=update_timestamp,
            new_state=OrderState.OPEN,
        )
        self._order_tracker.process_order_update(order_update)

        return exchange_order_id

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        exchange_symbol = await self.exchange_symbol_associated_to_pair(trading_pair=tracked_order.trading_pair)
        normalized_market = str(exchange_symbol).upper()
        exchange_order_id = self._exchange_order_id_for_cancel(tracked_order)
        if exchange_order_id is None:
            raise IOError(f"Unable to cancel order {tracked_order.client_order_id}: exchange order id unavailable.")
        return await self._cancel_order_by_uuid(normalized_market, exchange_order_id, tracked_order.client_order_id)

    def _exchange_order_id_for_cancel(self, tracked_order: InFlightOrder) -> Optional[str]:
        exchange_order_id = tracked_order.exchange_order_id
        if exchange_order_id:
            return exchange_order_id
        self.logger().warning(
            f"Exchange order id missing for {tracked_order.client_order_id}; cancel request cannot be sent."
        )
        return None

    def _get_fee(
            self,
            base_currency: str,
            quote_currency: str,
            order_type: OrderType,
            order_side: TradeType,
            amount: Decimal,
            price: Decimal = s_decimal_NaN,
            is_maker: Optional[bool] = None) -> TradeFeeBase:
        is_maker = order_type is OrderType.LIMIT_MAKER
        return DeductedFromReturnsTradeFee(percent=self.estimate_fee_pct(is_maker))

    async def _update_trading_fees(self):
        pass

    async def _iter_user_event_queue(self) -> AsyncIterable[Dict[str, Any]]:
        while True:
            try:
                yield await self._user_stream_tracker.user_stream.get()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Error reading YellowPro user stream queue. Retrying in 1s.")
                await self._sleep(1.0)

    async def _user_stream_event_listener(self):
        async for event_message in self._iter_user_event_queue():
            try:
                # Handle case when event_message is a string (JSON)
                if isinstance(event_message, str):
                    self.logger().debug(f"Processing string message from user stream: {event_message[:200]}...")
                    try:
                        event_message = json.loads(event_message)
                        self.logger().debug("Successfully parsed JSON string")
                    except json.JSONDecodeError as e:
                        self.logger().error(f"Invalid JSON message: {event_message}")
                        self.logger().error(f"JSON decode error: {e}")
                        # Try to extract useful info for debugging
                        if event_message.strip().startswith('{') and event_message.strip().endswith('}'):
                            self.logger().error("Message appears to be JSON but failed to parse")
                        continue

                # Ensure event_message is a dictionary
                if not isinstance(event_message, dict):
                    self.logger().error(f"Unexpected message type: {type(event_message)}, message: {event_message}")
                    continue

                push = event_message.get("push", {})
                if not push:
                    continue

                # Ensure push is a dictionary
                if not isinstance(push, dict):
                    self.logger().error(f"Invalid push data type: {type(push)}, push: {push}")
                    continue

                channel = push.get("channel", "")
                data = push.get("pub", {}).get("data", {})
                if "private" not in channel:
                    continue
                header = data.get("header", {})
                event_type = header.get("type", "")
                normalized_event_type = str(event_type or "").lower()
                if normalized_event_type in {"order.updated", "order.cancelled", "order.canceled", "order.expired"}:
                    await self._handle_order_update_event(data)
                elif normalized_event_type in {
                        "margin_account.balance_update",
                        "spot_account.balance_update"}:
                    self._handle_balance_update_event(data)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Unexpected error processing YellowPro user stream event.")
                await self._sleep(1.0)

    def _handle_balance_update_event(self, event: Dict[str, Any]):
        asset = event.get("asset_symbol")
        if asset is None:
            return
        available = Decimal(event.get("available_balance", "0"))
        total = Decimal(event.get("margin_balance", event.get("total_balance", "0")))
        self._account_available_balances[asset] = available
        self._account_balances[asset] = total

    async def _handle_order_update_event(self, event: Dict[str, Any]):
        order_uuid = event.get("uuid") or event.get("order_uuid")
        if order_uuid is None:
            return
        exchange_order_id = str(order_uuid)
        numeric_id = event.get("id")
        if numeric_id is not None:
            self._order_numeric_ids[exchange_order_id] = str(numeric_id)
        tracked_order = self._order_tracker.all_updatable_orders_by_exchange_order_id.get(exchange_order_id)
        if tracked_order is None:
            return
        market = event.get("market")
        trading_pair = tracked_order.trading_pair
        if market is not None:
            try:
                trading_pair = await self.trading_pair_associated_to_exchange_symbol(symbol=market)
            except Exception:
                trading_pair = tracked_order.trading_pair
        remaining = Decimal(event.get("amount", "0"))
        origin = Decimal(event.get("origin_amount", remaining))
        filled_total = origin - remaining
        executed_diff = filled_total - tracked_order.executed_amount_base
        price = Decimal(event.get("price", tracked_order.price or s_decimal_0))
        header = event.get("header", {})
        timestamp = header.get("created_at")
        update_timestamp = self._parse_iso_timestamp(timestamp) if timestamp else self.current_timestamp

        state = event.get("state", "")
        order_state = CONSTANTS.ORDER_STATE.get(state, tracked_order.current_state)

        order_update = OrderUpdate(
            trading_pair=trading_pair,
            update_timestamp=update_timestamp,
            new_state=order_state,
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=exchange_order_id,
        )
        self._order_tracker.process_order_update(order_update)

        if executed_diff > Decimal("0"):
            is_maker = self._is_maker_from_payload(event)
            fee = TradeFeeBase.new_spot_fee(
                fee_schema=self.trade_fee_schema(),
                trade_type=tracked_order.trade_type,
                percent=self.estimate_fee_pct(is_maker),
            )
            trade_update = TradeUpdate(
                trade_id=str(event.get("id") or header.get("id") or exchange_order_id),
                client_order_id=tracked_order.client_order_id,
                exchange_order_id=exchange_order_id,
                trading_pair=trading_pair,
                fill_base_amount=executed_diff,
                fill_quote_amount=executed_diff * price,
                fill_price=price,
                fee=fee,
                fill_timestamp=update_timestamp,
            )
            self._order_tracker.process_trade_update(trade_update)

            # Record first trade time
            safe_ensure_future(self._perf_tracer.record_event(
                order_id=tracked_order.client_order_id,
                event_type="first_trade_time",
                timestamp=time.time(),
                overwrite=False
            ))

    async def _update_trade_history(self):
        trades = await self._download_trades_snapshot(force_refresh=True)
        for trade in trades:
            trade_id = str(trade.get("id"))
            if trade_id in self._processed_trade_ids_lookup:
                continue
            self._processed_trade_ids.append(trade_id)
            self._processed_trade_ids_lookup.add(trade_id)
            while len(self._processed_trade_ids) > self._processed_trade_ids_limit:
                old_id = self._processed_trade_ids.popleft()
                self._processed_trade_ids_lookup.discard(old_id)
            await self._process_rest_trade(trade)

    async def _process_rest_trade(self, trade: Dict[str, Any]):
        order_uuid = trade.get("order_uuid")
        tracked_order = None
        if order_uuid is not None:
            tracked_order = self._order_tracker.all_fillable_orders_by_exchange_order_id.get(str(order_uuid))
        if tracked_order is None:
            numeric_order_id = str(trade.get("order_id"))
            for exchange_id, stored_numeric_id in self._order_numeric_ids.items():
                if stored_numeric_id == numeric_order_id:
                    tracked_order = self._order_tracker.all_fillable_orders_by_exchange_order_id.get(exchange_id)
                    break
        if tracked_order is None:
            return
        trading_pair = tracked_order.trading_pair
        amount = Decimal(trade.get("amount", "0"))
        price = Decimal(trade.get("price", "0"))
        is_maker = self._is_maker_from_payload(trade)
        executed_at = self._parse_iso_timestamp(trade.get("executed_at"))
        fee = TradeFeeBase.new_spot_fee(
            fee_schema=self.trade_fee_schema(),
            trade_type=tracked_order.trade_type,
            percent=self.estimate_fee_pct(is_maker),
        )
        trade_update = TradeUpdate(
            trade_id=str(trade.get("id")),
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=tracked_order.exchange_order_id,
            trading_pair=trading_pair,
            fill_base_amount=amount,
            fill_quote_amount=amount * price,
            fill_price=price,
            fee=fee,
            fill_timestamp=executed_at,
        )
        self._order_tracker.process_trade_update(trade_update)

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        exchange_symbol = await self.exchange_symbol_associated_to_pair(trading_pair=tracked_order.trading_pair)
        normalized_market = str(exchange_symbol).upper()
        orders = await self._download_orders_snapshot(
            normalized_market,
            force_refresh=False,
            target_order_id=tracked_order.exchange_order_id,
        )
        target_order = self._find_order_in_snapshot(orders, tracked_order.exchange_order_id)
        if target_order is None:
            orders = await self._download_orders_snapshot(
                normalized_market,
                force_refresh=True,
                target_order_id=tracked_order.exchange_order_id,
            )
            target_order = self._find_order_in_snapshot(orders, tracked_order.exchange_order_id)
        if target_order is None:
            raise IOError(f"Order {tracked_order.exchange_order_id} not found.")
        numeric_id = str(target_order.get("id"))
        self._order_numeric_ids[tracked_order.exchange_order_id] = numeric_id
        state = target_order.get("state")
        order_state = CONSTANTS.ORDER_STATE.get(state, tracked_order.current_state)
        updated_at = self._parse_iso_timestamp(target_order.get("updated_at"))
        return OrderUpdate(
            trading_pair=tracked_order.trading_pair,
            update_timestamp=updated_at,
            new_state=order_state,
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=tracked_order.exchange_order_id,
        )

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        numeric_id = self._order_numeric_ids.get(order.exchange_order_id)
        trades = await self._download_trades_snapshot(
            force_refresh=False,
            stop_when_no_new=False,
        )
        updates = [
            update for trade in trades
            if (update := self._trade_update_from_record(trade, order, numeric_id)) is not None
        ]
        if updates:
            return updates
        trades = await self._download_trades_snapshot(
            force_refresh=True,
            stop_when_no_new=False,
        )
        return [
            update for trade in trades
            if (update := self._trade_update_from_record(trade, order, numeric_id)) is not None
        ]

    async def _status_polling_loop_fetch_updates(self):
        await safe_gather(
            self._update_trade_history(),
            self._update_order_status(),
            self._update_balances(),
        )

    @staticmethod
    def _parse_iso_timestamp(iso_timestamp: Optional[str]) -> float:
        if iso_timestamp is None:
            return 0
        try:
            from datetime import datetime
            return datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0

    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal:
        """
        Gets price by type for YellowPro exchange. For test trading pairs like BTC-YTEST.USD,
        converts YTEST.USD to USDT and queries the real market price.

        :param trading_pair: The market trading pair
        :param price_type: The price type
        :returns The price
        """
        # Convert YTEST.USD pairs to USDT pairs for real price queries
        if "YTEST.USD" in trading_pair:
            # Convert BTC-YTEST.USD to BTC-USDT for price querying
            usdt_trading_pair = trading_pair.replace("YTEST.USD", "USDT")
            self.logger().debug(f"Converting {trading_pair} to {usdt_trading_pair} for price query")

            try:
                # First try to get price from order book with USDT pair
                return super().get_price_by_type(usdt_trading_pair, price_type)
            except Exception as e:
                self.logger().debug(f"Order book price not available for {usdt_trading_pair}: {e}")

                # Fallback to rate oracle with USDT pair
                from hummingbot.core.rate_oracle.rate_oracle import RateOracle
                rate_oracle = RateOracle.get_instance()
                rate = rate_oracle.get_pair_rate(usdt_trading_pair)
                if rate is not None:
                    self.logger().debug(f"Using rate oracle price for {usdt_trading_pair}: {rate}")
                    return rate

                # If still no rate found, use a reasonable fallback
                self.logger().warning(f"No rate found for {usdt_trading_pair}, using fallback price.")
                # For BTC-USDT, use a reasonable market price as fallback
                if "BTC" in trading_pair:
                    return Decimal("100000")  # 100K USDT per BTC
                else:
                    return Decimal("1")  # Default fallback

        # For non-test pairs, try to get the price from the order book
        try:
            return super().get_price_by_type(trading_pair, price_type)
        except Exception as e:
            self.logger().warning(f"Could not get price for {trading_pair}: {e}. Falling back to rate oracle.")
            # Fallback to rate oracle if order book is not available
            from hummingbot.core.rate_oracle.rate_oracle import RateOracle
            rate_oracle = RateOracle.get_instance()
            rate = rate_oracle.get_pair_rate(trading_pair)
            if rate is not None:
                return rate
            # If still no rate found, return 1 as a last resort
            self.logger().warning(f"No rate found for {trading_pair}, using fallback price of 1.")
            return Decimal("1")
