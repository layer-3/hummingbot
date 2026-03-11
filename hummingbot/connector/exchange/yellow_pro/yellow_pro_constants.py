from decimal import Decimal
from typing import Dict

from hummingbot.core.api_throttler.data_types import LinkedLimitWeightPair, RateLimit
from hummingbot.core.data_type.in_flight_order import OrderState

EXCHANGE_NAME = "yellow_pro"

PRODUCTION_DOMAIN = "production"

DOMAIN = PRODUCTION_DOMAIN

REST_URLS = {
    PRODUCTION_DOMAIN: "https://api.production.yellow.pro.neodax.app",
}

QUOTE_URLS = {
    PRODUCTION_DOMAIN: "https://api.production.yellow.pro.neodax.app",
}

WS_URLS = {
    PRODUCTION_DOMAIN: "wss://api.production.yellow.pro.neodax.app/api/v1/ranger/public/",
}

BROKER_ID = "HBOT"
MAX_ORDER_ID_LEN = 36
MARKET_ORDER_SLIPPAGE = Decimal("0.05")

CHECK_NETWORK_URL = "/health"

TICKER_PRICE_CHANGE_URL = "/ticker/24hr"
SNAPSHOT_REST_URL = "/orderbook"
EXCHANGE_INFO_URL = "/spot/exchangeInfo"

ACCOUNT_INFO_URL = "/spot/account"
ACCOUNT_LIST_URL = "/spot/accounts"
OPEN_ORDERS_URL = "/spot/open_orders"
ORDERS_URL = "/spot/orders"
TRADES_URL = "/spot/trades"

CREATE_ORDER_URL = "/spot/order"
CANCEL_ORDER_URL = "/spot/order"

POSITIONS_URL = "/positions"

TRADE_EVENT_TYPE = "trade"
DIFF_EVENT_TYPE = "order_book"

ORDER_STATE: Dict[str, OrderState] = {
    "wait": OrderState.OPEN,
    "pending": OrderState.OPEN,
    "partial": OrderState.PARTIALLY_FILLED,
    "done": OrderState.FILLED,
    "filled": OrderState.FILLED,
    "cancel": OrderState.CANCELED,
    "cancelled": OrderState.CANCELED,
    "canceled": OrderState.CANCELED,
    "expired": OrderState.CANCELED,  # Handle expired orders (FOK/IOC that couldn't fill)
    "rejected": OrderState.FAILED,
    "failed": OrderState.FAILED,
}

UNKNOWN_ORDER_MESSAGE = "order not found"
ORDER_NOT_EXIST_MESSAGE = "order_not_found"

ALL_ENDPOINTS_LIMIT = "all"
MAX_REQUESTS_PER_MINUTE = 1200

RATE_LIMITS = [
    RateLimit(ALL_ENDPOINTS_LIMIT, limit=MAX_REQUESTS_PER_MINUTE, time_interval=60),
    RateLimit(
        limit_id=SNAPSHOT_REST_URL,
        limit=MAX_REQUESTS_PER_MINUTE,
        time_interval=60,
        linked_limits=[LinkedLimitWeightPair(ALL_ENDPOINTS_LIMIT)],
    ),
    RateLimit(
        limit_id=TICKER_PRICE_CHANGE_URL,
        limit=MAX_REQUESTS_PER_MINUTE,
        time_interval=60,
        linked_limits=[LinkedLimitWeightPair(ALL_ENDPOINTS_LIMIT)],
    ),
    RateLimit(
        limit_id=EXCHANGE_INFO_URL,
        limit=MAX_REQUESTS_PER_MINUTE,
        time_interval=60,
        linked_limits=[LinkedLimitWeightPair(ALL_ENDPOINTS_LIMIT)],
    ),
    RateLimit(
        limit_id=ACCOUNT_INFO_URL,
        limit=MAX_REQUESTS_PER_MINUTE,
        time_interval=60,
        linked_limits=[LinkedLimitWeightPair(ALL_ENDPOINTS_LIMIT)],
    ),
    RateLimit(
        limit_id=OPEN_ORDERS_URL,
        limit=MAX_REQUESTS_PER_MINUTE,
        time_interval=60,
        linked_limits=[LinkedLimitWeightPair(ALL_ENDPOINTS_LIMIT)],
    ),
    RateLimit(
        limit_id=TRADES_URL,
        limit=MAX_REQUESTS_PER_MINUTE,
        time_interval=60,
        linked_limits=[LinkedLimitWeightPair(ALL_ENDPOINTS_LIMIT)],
    ),
    RateLimit(
        limit_id=CREATE_ORDER_URL,
        limit=MAX_REQUESTS_PER_MINUTE,
        time_interval=60,
        linked_limits=[LinkedLimitWeightPair(ALL_ENDPOINTS_LIMIT)],
    ),
    RateLimit(
        limit_id=CANCEL_ORDER_URL,
        limit=MAX_REQUESTS_PER_MINUTE,
        time_interval=60,
        linked_limits=[LinkedLimitWeightPair(ALL_ENDPOINTS_LIMIT)],
    ),
    RateLimit(
        limit_id=POSITIONS_URL,
        limit=MAX_REQUESTS_PER_MINUTE,
        time_interval=60,
        linked_limits=[LinkedLimitWeightPair(ALL_ENDPOINTS_LIMIT)],
    ),
]

HEARTBEAT_TIME_INTERVAL = 30.0
