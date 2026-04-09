# YellowPro API Documentation

## Overview

The YellowPro platform provides comprehensive RESTfull APIs for trading operations, account management, and market data access. The system consists of one main service:

- **Trading API Service**: Provides trading operations and market data access

### Authentication Methods

All authenticated endpoints support API Key authentication:

**API Key Authentication**
- Include API key headers:
  - `X-API-Key`: Your API key
  - `X-Signature`: HMAC signature of the request
  - `X-Timestamp`: Request timestamp in milliseconds
- Supported by all authenticated endpoints (spot, clearnet)

---

# Trading API Service

## Health & System Endpoints

### GET /health

Check if the trading API service is running and healthy.

**Authentication**: Not required

**Response**:
```json
{
  "status": "UP"
}
```

**Status Codes**:
- `200` - Service is healthy
- `500` - Service is unhealthy

---

## Market Data

### GET /exchangeInfo

Retrieve exchange information including available markets and their details.

**Authentication**: Not required

**Response**:
```json
{
  "timezone": "UTC",
  "server_time": 1640995200000,
  "symbols": [
    {
      "symbol": "BTCUSD",
      "status": "TRADING",
      "base_asset": "BTC",
      "base_asset_precision": 8,
      "quote_asset": "USD",
      "quote_asset_precision": 8,
      "maker_fee_rate": 0.001,
      "taker_fee_rate": 0.002
    }
  ]
}
```

**Response Fields**:
- `timezone`: Exchange timezone
- `server_time`: Current server timestamp in milliseconds
- `symbols`: Array of available trading symbols
  - `symbol`: Trading symbol name
  - `status`: Market status (e.g., "TRADING")
  - `base_asset`: Base asset symbol
  - `base_asset_precision`: Decimal precision for base asset
  - `quote_asset`: Quote asset symbol
  - `quote_asset_precision`: Decimal precision for quote asset
  - `maker_fee_rate`: Maker fee rate
  - `taker_fee_rate`: Taker fee rate

**Status Codes**:
- `200` - Exchange information retrieved successfully
- `500` - Internal server error

### GET /orderbook

Retrieve order book data for a specific trading symbol.

**Authentication**: Not required

**Query Parameters**:
- `symbol` (string, required): Trading symbol to get order book for

**Request**:
```http
GET /orderbook?symbol=BTCUSD
```

**Response**:
```json
{
  "bids": [
    ["35000", "1.5"],
    ["34999", "2.0"]
  ],
  "asks": [
    ["35001", "1.2"],
    ["35002", "0.8"]
  ]
}
```

**Response Fields**:
- `bids`: Array of bid levels, each containing [price, amount]
- `asks`: Array of ask levels, each containing [price, amount]

**Status Codes**:
- `200` - Order book retrieved successfully
- `400` - Missing symbol parameter
- `404` - Symbol not found
- `500` - Internal server error

### GET /klines

Retrieve OHLC/candlestick data for a specific trading symbol. Compatible with Binance API format.

**Authentication**: Not required

**Query Parameters**:
- `symbol` (string, required): Trading symbol to get klines for
- `interval` (string, optional): Kline interval - `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`, `3d`, `1w`, `1M`
- `startTime` (integer, optional): Start time in milliseconds since Unix epoch
- `endTime` (integer, optional): End time in milliseconds since Unix epoch
- `limit` (integer, optional): Number of klines to return (default: 500, max: 1000)
- `timeZone` (string, optional): Timezone offset (e.g., "UTC", "+08:00")

**Request**:
```http
GET /klines?symbol=BTCUSD&interval=1h&limit=24
```

**Response**:
```json
[
  [
    1759224600000,
    "113216.5",
    "113300.1",
    "112888",
    "112945.1",
    "2602.44",
    41934
  ]
]
```

**Response Format** (each kline array contains):
- `[0]`: Open time (milliseconds)
- `[1]`: Open price
- `[2]`: High price
- `[3]`: Low price
- `[4]`: Close price
- `[5]`: Volume
- `[6]`: Number of trades

**Status Codes**:
- `200` - Klines retrieved successfully
- `400` - Invalid parameters or missing symbol
- `404` - Symbol not found
- `500` - Internal server error

### GET /ticker/24hr

Retrieve 24-hour ticker statistics for a specific trading symbol.

**Authentication**: Not required

**Query Parameters**:
- `symbol` (string, required): Trading symbol to get ticker for

**Request**:
```http
GET /ticker/24hr?symbol=BTCUSD
```

**Response**:
```json
{
  "marketId": "BTCUSD",
  "time": 1759228650754,
  "min": "111844.3",
  "max": "114188",
  "first": "112024.5",
  "last": "113964.6",
  "volume": "66457.75557923",
  "quoteVolume": "7528165635.780153719",
  "vwap": "113277.4582916087302791",
  "priceChange": "+1.73%"
}
```

**Response Fields**:
- `marketId`: Market identifier (e.g., BTCUSD)
- `time`: Timestamp of the data in Unix milliseconds
- `min`: Lowest price in the period
- `max`: Highest price in the period
- `first`: First price in the period (open price)
- `last`: Last trade price
- `volume`: Total traded volume in the base currency
- `quoteVolume`: Total traded volume in the quote currency
- `vwap`: Volume-weighted average price
- `priceChange`: Price change percentage over the period

**Status Codes**:
- `200` - Ticker retrieved successfully
- `400` - Missing symbol parameter
- `404` - Symbol not found
- `500` - Internal server error

---

# Quote Service API

The Quote Service provides real-time market data endpoints for prices, technical indicators, and market analysis data.

## Health Endpoints

### GET /health

Check if the quote service is running and healthy.

**Authentication**: Not required

**Response**:
```json
{
  "status": "UP"
}
```

**Status Codes**:
- `200` - Service is healthy
- `500` - Service is unhealthy

---

## Market Data Endpoints

### GET /data/price

Retrieve the latest price observation for all markets.

**Authentication**: Not required

**Response**:
```json
{
  "header": {
    "event_id": "550e8400-e29b-41d4-a716-446655440000",
    "timestamp": "2023-12-07T10:30:00Z"
  },
  "market": "BTCUSD",
  "price": "35000.50000000"
}
```

**Response Fields**:
- `header`: Event metadata
  - `event_id`: Unique event identifier
  - `timestamp`: Event timestamp
- `market`: Market symbol (e.g., "BTCUSD")
- `price`: Latest observed price

**Status Codes**:
- `200` - Latest price retrieved successfully
- `204` - No price data available
- `500` - Internal server error

### GET /data/momentum

Retrieve the latest momentum indicator data for all markets.

**Authentication**: Not required

**Request**:
```http
GET /data/momentum
```

**Response**:
```json
{
  "Market": {
    "Base": "btc",
    "Quote": "usd"
  },
  "Value": "0.0023",
  "Time": "2025-09-30T15:40:54.455631184Z"
}
```

**Response Fields**:
- `Market`: Market information
  - `Base`: Base currency (e.g., "btc")
  - `Quote`: Quote currency (e.g., "usd")
- `Value`: Momentum indicator value (decimal)
- `Time`: Timestamp when the indicator was computed (ISO 8601 format)

**Status Codes**:
- `200` - Latest momentum retrieved successfully
- `204` - No momentum data available
- `500` - Internal server error

### GET /data/std

Retrieve the latest moving standard deviation data for all markets.

**Authentication**: Not required

**Request**:
```http
GET /data/std
```

**Response**:
```json
{
  "Market": {
    "Base": "btc",
    "Quote": "usd"
  },
  "Value": "145.67",
  "Time": "2025-09-30T15:40:54.455631184Z"
}
```

**Response Fields**:
- `Market`: Market information
  - `Base`: Base currency (e.g., "btc")
  - `Quote`: Quote currency (e.g., "usd")
- `Value`: Moving standard deviation value (decimal)
- `Time`: Timestamp when the indicator was computed (ISO 8601 format)

**Status Codes**:
- `200` - Latest standard deviation retrieved successfully
- `204` - No standard deviation data available
- `500` - Internal server error

### GET /data/vwma

Retrieve the latest volume-weighted moving average (VWMA) data for all markets.

**Authentication**: Not required

**Request**:
```http
GET /data/vwma
```

**Response**:
```json
{
  "Market": {
    "Base": "btc",
    "Quote": "usd"
  },
  "Value": "35250.45",
  "Time": "2025-09-30T15:40:54.455631184Z"
}
```

**Response Fields**:
- `Market`: Market information
  - `Base`: Base currency (e.g., "btc")
  - `Quote`: Quote currency (e.g., "usd")
- `Value`: Volume-weighted moving average value (decimal)
- `Time`: Timestamp when the indicator was computed (ISO 8601 format)

**Status Codes**:
- `200` - Latest VWMA retrieved successfully
- `204` - No VWMA data available
- `500` - Internal server error

---

## Development/Testing Endpoints

**⚠️ WARNING**: The following endpoints are only available in development environments and allow price manipulation. They should never be used in production.

### POST /bitfaker/submit

Submit fake trade events for testing purposes (development only).

**Authentication**: Not required
**Environment**: Development only

**Request Body**:
```json
{
  "market": "btc/usd",
  "price": "53000",
  "volume": "1",
  "total": "53000",
  "taker_type": "sell",
  "created_at": "2025-09-07T15:22:28Z"
}
```

**Response**: Empty body with status code

**Status Codes**:
- `202` - Trade event accepted for processing
- `400` - Invalid request format or submission failed
- `500` - Internal server error

### POST /bitfaker/tick

Trigger market ticks for testing purposes (development only).

**Authentication**: Not required
**Environment**: Development only

**Request Body**: No body

**Response**: Empty body with status code

**Status Codes**:
- `202` - Tick event accepted for processing
- `400` - Invalid request format or tick failed
- `500` - Internal server error

---

## Spot Trading

The spot trading API provides endpoints for managing spot trading accounts, placing and canceling orders, and retrieving trade history.

### GET /spot/exchangeInfo

Retrieve exchange information including available spot markets and their details.

**Authentication**: Not required

**Response**:
```json
{
  "timezone": "UTC",
  "server_time": 1640995200000,
  "symbols": [
    {
      "symbol": "BTCYTEST.USD",
      "status": "active",
      "base_asset": "BTC",
      "base_asset_precision": 8,
      "quote_asset": "YTEST.USD",
      "quote_asset_precision": 8
    }
  ]
}
```

**Response Fields**:
- `timezone`: Exchange timezone
- `server_time`: Current server timestamp in milliseconds
- `symbols`: Array of available spot trading symbols
  - `symbol`: Trading symbol name (e.g., "BTCYTEST.USD")
  - `status`: Market status (e.g., "active")
  - `base_asset`: Base asset symbol (e.g., "BTC")
  - `base_asset_precision`: Decimal precision for base asset
  - `quote_asset`: Quote asset symbol (e.g., "YTEST.USD")
  - `quote_asset_precision`: Decimal precision for quote asset

**Status Codes**:
- `200` - Exchange information retrieved successfully
- `500` - Internal server error

---

### GET /spot/accounts

Retrieve all spot accounts for the authenticated user.

**Authentication**: Required

**Response**:
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "app_session_id": "spot_session_123",
    "owner": "0x1234567890abcdef1234567890abcdef12345678",
    "balances": [
      {
        "asset_symbol": "BTC",
        "total_balance": "1.50000000",
        "available_balance": "1.20000000",
        "available_balance_usd": "135475.145235",
        "locked_balance": "0.30000000",
        "last_updated": "2023-12-07T10:30:00.000000Z"
      },
      {
        "asset_symbol": "USDT",
        "total_balance": "10000.00000000",
        "available_balance": "8500.00000000",
        "available_balance_usd": "8500.00000000",
        "locked_balance": "1500.00000000",
        "last_updated": "2023-12-07T10:30:00.000000Z"
      }
    ],
    "state": "active",
    "opened_at": "2023-12-01T08:00:00.000000Z"
  }
]
```

**Response Fields**:
- `id`: Unique spot account identifier (UUID)
- `app_session_id`: Application session identifier
- `owner`: Owner's Ethereum wallet address
- `balances`: Array of balance objects
  - `asset_symbol`: Asset symbol (e.g., "BTC", "USDT")
  - `total_balance`: Total balance (available + locked)
  - `available_balance`: Balance available for trading
  - `available_balance_usd`: Available balance equivalent in USD
  - `locked_balance`: Balance locked in open orders
  - `last_updated`: Last balance update timestamp
- `state`: Account state (`active`, `closed`)
- `opened_at`: Account creation timestamp

**Status Codes**:
- `200` - Accounts retrieved successfully
- `401` - Authentication failed
- `500` - Internal server error

### GET /spot/account

Retrieve information for a specific spot account.

**Authentication**: Required

**Query Parameters**:
- `app_session_id` (string, required): Spot account app session ID
- `asset` (string, optional): Case-insensitive fuzzy filter on `asset_symbol` (contains match)

**Request**:
```http
GET /spot/account?app_session_id=spot_session_123
```

**Response**:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "app_session_id": "spot_session_123",
  "owner": "0x1234567890abcdef1234567890abcdef12345678",
  "balances": [
    {
      "asset_symbol": "BTC",
      "total_balance": "1.50000000",
      "available_balance": "1.20000000",
      "available_balance_usd": "135475.145235",
      "locked_balance": "0.30000000",
      "last_updated": "2023-12-07T10:30:00.000000Z"
    }
  ],
  "state": "active",
  "opened_at": "2023-12-01T08:00:00.000000Z"
}
```

**Status Codes**:
- `200` - Account retrieved successfully
- `400` - Missing app_session_id parameter
- `401` - Authentication failed
- `404` - Account not found
- `500` - Internal server error

### POST /spot/order

Create a new spot trading order.

**Authentication**: Required

**Request Body**:
```json
{
  "app_session_id": "spot_session_123",
  "market": "BTCUSDT",
  "side": "buy",
  "type": "limit",
  "amount": "0.5",
  "price": "35000",
  "time_in_force": "gtc"
}
```

**Request Parameters**:
- `app_session_id` (string, required): Spot account app session ID
- `market` (string, required): Trading market (e.g., "BTCUSDT", "ETHUSDC")
- `side` (string, required): Order side - `buy` or `sell`
- `type` (string, required): Order type - `limit` or `market`
- `amount` (string, required): Order amount in base asset (decimal format)
- `price` (string, optional): Limit price (required for limit orders, ignored for market orders)
- `time_in_force` (string, optional): Time in force - `gtc` (Good-Till-Cancelled), `ioc` (Immediate-Or-Cancel), `fok` (Fill-Or-Kill). Defaults to `GTC` for limit orders, `IOC` for market orders.

**Response**:
```json
{
  "order_uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response Fields**:
- `order_uuid`: UUID of the created order

**Status Codes**:
- `200` - Order created successfully
- `400` - Invalid request parameters or validation failed
- `401` - Authentication failed
- `500` - Internal server error

### DELETE /spot/order

Cancel an existing spot order.

**Authentication**: Required

**Request Body**:
```json
{
  "app_session_id": "spot_session_123",
  "market": "BTCUSDT",
  "order_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "type": "limit"
}
```

**Request Parameters**:
- `app_session_id` (string, required): Spot account app session ID
- `market` (string, required): Trading market
- `order_uuid` (string, required): UUID of the order to cancel
- `type` (string, required): Order type


**Response**:
```json
{
  "message": "Spot order cancellation request sent successfully"
}
```

**Status Codes**:
- `200` - Cancellation request sent successfully
- `400` - Invalid request or missing parameters
- `401` - Authentication failed
- `500` - Internal server error

### GET /spot/open_orders

Retrieve open spot orders with pagination.

**Authentication**: Required

**Query Parameters**:
- `app_session_id` (string, required): Spot account app session ID
- `asset` (string, optional): Case-insensitive fuzzy filter on `asset_symbol` (contains match)
- `market` (string, optional): Filter by trading market
- `page` (integer, optional): Page number (default: 1)
- `page_size` (integer, optional): Number of orders per page (default: 50, max: 100)

**Request**:
```http
GET /spot/open_orders?app_session_id=spot_session_123&market=BTCUSDT&page=1&page_size=20
```

**Response**:
```json
{
  "orders": [
    {
      "id": "1234",
      "order_id": "550e8400-e29b-41d4-a716-446655440000",
      "channel_id": "spot_session_123",
      "market": "BTCUSDT",
      "price": "35000.00000000",
      "amount": "0.50000000",
      "origin_amount": "0.50000000",
      "notional": "17500.00000000",
      "side": "buy",
      "type": "limit",
      "state": "wait",
      "event": "",
      "reason": "",
      "created_at": "2023-12-07T10:30:00.000000Z",
      "updated_at": "2023-12-07T10:30:00.000000Z",
      "completed_at": ""
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

**Response Fields**:
- `orders`: Array of order objects
  - `id`: Internal order record ID
  - `order_id`: Order UUID
  - `channel_id`: Spot account app session ID
  - `market`: Trading market
  - `price`: Order price
  - `amount`: Current order amount (may be partially filled)
  - `origin_amount`: Original order amount
  - `notional`: Notional value (amount x price)
  - `side`: Order side (`buy` or `sell`)
  - `type`: Order type (`limit` or `market`)
  - `state`: Order state (`wait`, `done`, `canceled`)
  - `event`: Last order event (empty string)
  - `reason`: Reason for last state change (empty string)
  - `created_at`: Order creation timestamp
  - `updated_at`: Last update timestamp
  - `completed_at`: Completion timestamp (empty for open orders)
- `total`: Total number of matching orders
- `page`: Current page number
- `page_size`: Number of orders per page

**Status Codes**:
- `200` - Orders retrieved successfully
- `400` - Invalid query parameters
- `401` - Authentication failed
- `500` - Internal server error

### GET /spot/orders

Retrieve spot order history with pagination.

**Authentication**: Required

**Query Parameters**:
- `app_session_id` (string, required): Spot account app session ID
- `market` (string, optional): Filter by trading market
- `page` (integer, optional): Page number (default: 1)
- `page_size` (integer, optional): Number of orders per page (default: 50, max: 100)

**Request**:
```http
GET /spot/orders?app_session_id=spot_session_123&page=1&page_size=50
```

**Response**: Same format as `/spot/open_orders` but includes all orders (open, filled, and cancelled)

**Status Codes**:
- `200` - Orders retrieved successfully
- `400` - Invalid query parameters
- `401` - Authentication failed
- `500` - Internal server error

### GET /spot/trades

Retrieve spot trade history with pagination.

**Authentication**: Required

**Query Parameters**:
- `app_session_id` (string, required): Spot account app session ID
- `market` (string, optional): Filter by trading market
- `page` (integer, optional): Page number (default: 1)
- `page_size` (integer, optional): Number of trades per page (default: 50, max: 100)

**Request**:
```http
GET /spot/trades?app_session_id=spot_session_123&market=BTCUSDT&page=1&page_size=50
```

**Response**:
```json
{
  "trades": [
    {
      "id": "5721092",
      "order_id": "12345",
      "market": "BTCUSDT",
      "amount": "0.50000000",
      "price": "35000.00000000",
      "is_buyer": true,
      "is_maker": false,
      "executed_at": "2023-12-07T10:30:00.000000Z",
      "created_at": "2023-12-07T10:30:01.000000Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50
}
```

**Response Fields**:
- `trades`: Array of trade objects
  - `id`: Internal trade record ID (string)
  - `order_id`: User's own order ID in this trade (string, numeric)
  - `market`: Trading market
  - `amount`: Trade amount in base asset
  - `price`: Trade execution price
  - `is_buyer`: Whether the user was the buyer in this trade (boolean)
  - `is_maker`: Whether the user was the maker (liquidity provider) in this trade (boolean)
  - `executed_at`: Trade execution timestamp
  - `created_at`: Trade record creation timestamp
- `total`: Total number of matching trades
- `page`: Current page number
- `page_size`: Number of trades per page

**Note**: The API response only exposes the user's own order ID and does not expose counterparty information for privacy reasons. It provides user-relative flags (`is_buyer`, `is_maker`) to indicate the user's role in the trade.

**Status Codes**:
- `200` - Trades retrieved successfully
- `400` - Invalid query parameters
- `401` - Authentication failed
- `500` - Internal server error

### GET /spot/deposits

Retrieve spot deposit history.

**Authentication**: Required

**Query Parameters**:
- `app_session_id` (string, required): Spot account app session ID

**Request**:
```http
GET /spot/deposits?app_session_id=spot_session_123
```

**Response**:
```json
{
  "deposits": [
    {
      "spot_account_id": "550e8400-e29b-41d4-a716-446655440001",
      "asset_symbol": "USDT",
      "amount": "10000.00000000",
      "transaction_hash": "0xabcdef1234567890",
      "created_at": "2023-12-07T10:30:01.000000Z",
      "deposited_at": "2023-12-07T10:30:00.000000Z"
    }
  ],
  "total": 6,
  "page": 1,
  "page_size": 50
}
```

**Response Fields**:
- `deposits`: Array of deposit objects
  - `spot_account_id`: Spot account ID (UUID)
  - `asset_symbol`: Asset symbol (e.g., "USDT", "BTC")
  - `amount`: Deposit amount
  - `transaction_hash`: ClearNet transaction hash
  - `created_at`: Record creation timestamp
  - `deposited_at`: Timestamp when deposit occurred
- `total`: Total number of deposits matching criteria
- `page`: Current page number
- `page_size`: Number of deposits per page

**Status Codes**:
- `200` - Deposits retrieved successfully
- `400` - Missing app_session_id parameter
- `401` - Authentication failed
- `500` - Internal server error

### GET /spot/withdrawals

Retrieve spot withdrawal history.

**Authentication**: Required

**Query Parameters**:
- `app_session_id` (string, required): Spot account app session ID

**Request**:
```http
GET /spot/withdrawals?app_session_id=spot_session_123
```

**Response**:
```json
{
  "withdrawals": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "spot_account_id": "550e8400-e29b-41d4-a716-446655440001",
      "asset_symbol": "USDT",
      "amount": "5000.00000000",
      "status": "completed",
      "transaction_hash": "0xabcdef1234567890",
      "failure_reason": "",
      "requested_at": "2023-12-07T10:30:00.000000Z",
      "completed_at": "2023-12-07T10:31:00.000000Z",
      "created_at": "2023-12-07T10:30:01.000000Z"
    }
  ],
  "total": 0,
  "page": 1,
  "page_size": 50
}
```

**Response Fields**:
- `withdrawals`: Array of withdrawal objects
  - `id`: Withdrawal ID (UUID)
  - `spot_account_id`: Spot account ID (UUID)
  - `asset_symbol`: Asset symbol (e.g., "USDT", "BTC")
  - `amount`: Withdrawal amount
  - `status`: Withdrawal status (`pending`, `completed`, `failed`)
  - `transaction_hash`: ClearNet transaction hash (when completed)
  - `failure_reason`: Reason for failure (when status is `failed`)
  - `requested_at`: Timestamp when withdrawal was requested
  - `completed_at`: Timestamp when withdrawal completed (or failed)
  - `created_at`: Record creation timestamp
- `total`: Total number of withdrawals matching criteria
- `page`: Current page number
- `page_size`: Number of withdrawals per page

**Status Codes**:
- `200` - Withdrawals retrieved successfully
- `400` - Missing app_session_id parameter
- `401` - Authentication failed
- `500` - Internal server error

### POST /spot/withdrawal

Request a new spot withdrawal.

**Authentication**: Required

> **Known Issue**: Server returns `500` instead of `400` when withdrawal amount exceeds available balance.

**Request Body**:
```json
{
  "app_session_id": "spot_session_123",
  "asset_symbol": "USDT",
  "amount": "5000.00000000"
}
```

**Request Parameters**:
- `app_session_id` (string, required): Spot account app session ID
- `asset_symbol` (string, required): Asset to withdraw (e.g., "USDT", "BTC")
- `amount` (string, required): Amount to withdraw (decimal format, must be positive)

**Response**:
```json
{
  "withdrawal_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Withdrawal request accepted and funds reserved"
}
```

**Response Fields**:
- `withdrawal_id`: UUID of the withdrawal request
- `status`: Initial status (`pending`)
- `message`: Confirmation message

**Status Codes**:
- `200` - Withdrawal request accepted
- `400` - Invalid request or insufficient funds
- `401` - Authentication failed
- `500` - Internal server error

---

## Clearnet (Blockchain Integration)

The Clearnet API provides endpoints for managing blockchain transactions, deposits, and app sessions. These endpoints handle the integration between the exchange and the blockchain layer.

### GET /clearnet/sessions

Retrieve all clearnet app sessions for the authenticated user.

**Authentication**: Required

**Response**:
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "owner": "0x1234567890abcdef1234567890abcdef12345678",
    "channel_id": "0xchannel123",
    "state": "active",
    "created_at": "2023-12-01T08:00:00.000000Z"
  }
]
```

**Response Fields**:
- `id`: App session ID (UUID)
- `owner`: Owner's Ethereum wallet address
- `channel_id`: Channel ID associated with this session
- `state`: Session state (e.g., `active`, `closed`)
- `created_at`: Session creation timestamp

**Status Codes**:
- `200` - Sessions retrieved successfully
- `401` - Authentication failed
- `500` - Internal server error

---

### GET /clearnet/sessions/:session_id

Retrieve details for a specific clearnet app session.

**Authentication**: Required

**Path Parameters**:
- `session_id` (string, required): App session ID (UUID)

**Request**:
```http
GET /clearnet/sessions/550e8400-e29b-41d4-a716-446655440000
```

**Response**:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "owner": "0x1234567890abcdef1234567890abcdef12345678",
  "channel_id": "0xchannel123",
  "state": "active",
  "created_at": "2023-12-01T08:00:00.000000Z"
}
```

**Status Codes**:
- `200` - Session retrieved successfully
- `401` - Authentication failed
- `404` - Session not found
- `500` - Internal server error

---

### GET /clearnet/transactions

Retrieve blockchain transactions by transaction IDs.

**Authentication**: Required

**Query Parameters**:
- `ids` (string, required): Comma-separated list of transaction IDs

**Request**:
```http
GET /clearnet/transactions?ids=tx1,tx2,tx3
```

**Response**:
```json
[
  {
    "id": "tx1",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "type": "deposit",
    "asset_symbol": "USDT",
    "amount": "1000.00000000",
    "status": "completed",
    "transaction_hash": "0xabcdef1234567890",
    "created_at": "2023-12-07T10:30:00.000000Z",
    "completed_at": "2023-12-07T10:31:00.000000Z"
  }
]
```

**Response Fields**:
- `id`: Transaction ID
- `session_id`: App session ID (UUID)
- `type`: Transaction type (`deposit`, `withdrawal`)
- `asset_symbol`: Asset symbol
- `amount`: Transaction amount
- `status`: Transaction status (`pending`, `completed`, `failed`)
- `transaction_hash`: Blockchain transaction hash
- `created_at`: Transaction creation timestamp
- `completed_at`: Transaction completion timestamp

**Status Codes**:
- `200` - Transactions retrieved successfully
- `400` - Missing or invalid IDs parameter
- `401` - Authentication failed
- `500` - Internal server error

---

### GET /clearnet/sessions/:session_id/transactions

Retrieve all blockchain transactions for a specific app session.

**Authentication**: Required

**Path Parameters**:
- `session_id` (string, required): App session ID (UUID)

**Request**:
```http
GET /clearnet/sessions/550e8400-e29b-41d4-a716-446655440000/transactions
```

**Response**: Same format as `/clearnet/transactions`

**Status Codes**:
- `200` - Transactions retrieved successfully
- `401` - Authentication failed
- `404` - Session not found
- `500` - Internal server error

---

### GET /clearnet/session/payload

Get the payload data required for creating a new clearnet app session on the blockchain.

**Authentication**: Required

**Query Parameters**:
- `account_type` (string, required): Account type - `spot`
- `nonce` (integer, required): Nonce for transaction ordering

**Request**:
```http
GET /clearnet/session/payload?account_type=spot&nonce=1
```

**Response**:
```json
{
  "payload": "0x...",
  "expires_at": "2023-12-07T10:35:00Z"
}
```

**Response Fields**:
- `payload`: Hex-encoded payload data to be signed
- `expires_at`: When the payload expires

**Status Codes**:
- `200` - Payload generated successfully
- `400` - Invalid parameters
- `401` - Authentication failed
- `500` - Internal server error

---

### POST /clearnet/session

Create a new clearnet app session on the blockchain.

**Authentication**: Required

**Request Body**:
```json
{
  "account_type": "spot",
  "nonce": 1,
  "signature": "0x1234567890abcdef..."
}
```

**Request Parameters**:
- `account_type` (string, required): Account type - `spot`
- `nonce` (integer, required): Nonce used in payload generation
- `signature` (string, required): Ethereum signature of the payload

**Response**:
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "channel_id": "0xchannel123",
  "status": "pending",
  "message": "App session creation request submitted"
}
```

**Response Fields**:
- `session_id`: Created app session ID (UUID)
- `channel_id`: Channel ID for this session
- `status`: Initial status (`pending`)
- `message`: Confirmation message

**Status Codes**:
- `200` - Session creation request accepted
- `400` - Invalid request or signature verification failed
- `401` - Authentication failed
- `500` - Internal server error

---

### GET /clearnet/sessions/:session_id/deposit/payload

Get the payload data required for depositing funds to a clearnet app session.

**Authentication**: Required

**Path Parameters**:
- `session_id` (string, required): App session ID (UUID)

**Query Parameters**:
- `asset_symbol` (string, required): Asset to deposit (e.g., "USDT", "BTC")
- `amount` (string, required): Amount to deposit (decimal format)
- `nonce` (integer, required): Nonce for transaction ordering

**Request**:
```http
GET /clearnet/sessions/550e8400-e29b-41d4-a716-446655440000/deposit/payload?asset_symbol=USDT&amount=1000.00&nonce=1
```

**Response**:
```json
{
  "payload": "0x...",
  "expires_at": "2023-12-07T10:35:00Z"
}
```

**Response Fields**:
- `payload`: Hex-encoded payload data to be signed
- `expires_at`: When the payload expires

**Status Codes**:
- `200` - Payload generated successfully
- `400` - Invalid parameters
- `401` - Authentication failed
- `404` - Session not found
- `500` - Internal server error

---

### POST /clearnet/sessions/:session_id/deposit

Request a deposit to a clearnet app session.

**Authentication**: Required

**Path Parameters**:
- `session_id` (string, required): App session ID (UUID)

**Request Body**:
```json
{
  "asset_symbol": "USDT",
  "amount": "1000.00000000",
  "nonce": 1,
  "signature": "0x1234567890abcdef..."
}
```

**Request Parameters**:
- `asset_symbol` (string, required): Asset to deposit
- `amount` (string, required): Amount to deposit (decimal format)
- `nonce` (integer, required): Nonce used in payload generation
- `signature` (string, required): Ethereum signature of the payload

**Response**:
```json
{
  "deposit_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Deposit request submitted to blockchain"
}
```

**Response Fields**:
- `deposit_id`: Deposit transaction ID (UUID)
- `status`: Initial status (`pending`)
- `message`: Confirmation message

**Status Codes**:
- `200` - Deposit request accepted
- `400` - Invalid request or signature verification failed
- `401` - Authentication failed
- `404` - Session not found
- `500` - Internal server error

---

## Error Handling

All endpoints return consistent error responses with the following structure:

### Authentication Service Errors:
```json
{
  "error": "error_code",
  "message": "Human-readable error message",
  "code": 400
}
```

### Trading API Service Errors:
```json
{
  "error": "error_code",
  "message": "Detailed error description"
}
```

### Common Error Codes:

**Authentication:**
- `invalid_request` - Invalid request body format
- `internal_error` - Internal server error
- `invalid_signature` - Signature verification failed
- `unauthorized` - Authentication required

**Trading API Service:**
- `missing_parameter` - Required parameter is missing
- `invalid_request_format` - Request body format is invalid
- `validation_failed` - Request validation failed
- `missing_channel_id` - Channel ID parameter is required
- `missing_symbol` - Symbol parameter is required
- `symbol_not_found` - Requested symbol not found
- `missing_order_uuid` - Order UUID is required
- `invalid_order_uuid` - Order UUID format is invalid
- `internal_error` - Internal server error occurred

### HTTP Status Codes:
- `200` - Success
- `400` - Bad Request (client error)
- `401` - Unauthorized (authentication required/failed)
- `404` - Not Found (resource doesn't exist)
- `500` - Internal Server Error

---

## WebSocket API

The trading API also supports WebSocket connections for real-time data and RPC calls. WebSocket endpoints provide:

- Order status notifications
- Market data streams
- RPC methods for account operations

WebSocket documentation is available separately.


---

## Rate Limiting

Rate limiting is enforced to ensure fair usage:
- Authentication middleware includes rate limiting functionality
- Limits are configurable per endpoint and user type
- Rate limit headers are included in responses

---

## Data Types

### Decimal Values
All monetary and quantity values are represented as strings in decimal format to maintain precision:
```json
{
  "amount": "1.50000000",
  "price": "35000.00000000"
}
```

### Timestamps
Timestamps are provided in milliseconds since Unix epoch:
```json
{
  "server_time": 1640995200000
}
```

### UUIDs
Order UUIDs follow standard UUID format:
```json
{
  "order_uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

## Implementation Notes

### Event-Driven Architecture

The YellowPro platform uses an event-driven architecture with Kafka as the primary message bus:

**Spot Trading:**
- Spot order commands are published to Kafka topics
- Order execution events are consumed from Kafka
- Full Kafka integration completed

**Clearnet Integration:**
- Blockchain transactions are handled asynchronously
- Deposit and withdrawal events are published to Kafka
- Status updates are propagated through event streams

### Pagination Defaults

All paginated endpoints follow consistent defaults:

| Parameter | Default | Maximum |
|-----------|---------|---------|
| `page` | 1 | N/A |
| `page_size` | 50 | 100 |
| `limit` | 50 | 100 |
| `offset` | 0 | N/A |

**Note:** Transfer endpoints use `limit`/`offset` pagination, while other endpoints use `page`/`page_size`.

### Service Ports Reference

For local development:

| Service | HTTP Port | WebSocket Port |
|---------|-----------|----------------|
| Auth Service | 8081 | N/A |
| Trading API | 8086 | 8086 |
| Quote Service | 8084 | N/A |
| Ledger Service | 8083 | N/A |

### Known Limitations

1. **Clearnet Session Management:** App session creation requires blockchain interaction with associated latency
