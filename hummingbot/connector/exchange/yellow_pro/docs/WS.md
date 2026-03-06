# YellowPro WebSocket API Documentation

## Overview

The YellowPro WebSocket API provides real-time data streams, order notifications, account updates, and RPC method calls for trading operations. The WebSocket connection is established through the Trading API Service endpoint and supports both authenticated and unauthenticated connections.

## Connection Information

### Connection Requirements
- **Protocol**: WebSocket (RFC 6455) with Centrifugo connect handshake
- **Authentication**: Optional (supports both header-based and token-based authentication)
- **Reconnection**: Automatic with exponential backoff
- **Message Format**: JSON (server may send newline-delimited batched JSON, see [Message Batching](#message-batching))

---

## Connection Handshake (Required)

All WebSocket connections — including unauthenticated ones — **must** send a Centrifugo `connect` command immediately after opening the socket. A bare WebSocket connection without the handshake will be closed by the server with error `3501 (bad request)`.

### Unauthenticated Connect

```json
{"id": 1, "connect": {}}
```

**Success Response**:
```json
{
  "id": 1,
  "connect": {
    "client": "3d43bec7-0e0c-429c-a29f-8830bef64437",
    "data": {},
    "ping": 300,
    "pong": true
  }
}
```

### Authenticated Connect (Token-Based — Recommended)

Send token in the connect message payload after establishing the WebSocket connection:

```json
{
  "id": 1,
  "connect": {
    "token": "<JWT_TOKEN>"
  }
}
```

**Success Response**:
```json
{
  "id": 1,
  "connect": {
      "client": "3d43bec7-0e0c-429c-a29f-8830bef64437",
      "data": {},
      "subs": {
          "private.0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8": {
              "recoverable": true,
              "epoch": "bQsD",
              "positioned": true
          }
      },
      "ping": 300,
      "pong": true
  }
}
```

### Authenticated Connect (Header-Based — Node.js Only)

Include JWT Bearer token in the WebSocket connection headers, then still send the connect command:

**Node.js Example (using `ws` library)**:
```javascript
const ws = new WebSocket(WS_ENDPOINT, [], {
  headers: {
    'Authorization': 'Bearer <JWT_TOKEN>'
  }
});

// Still must send connect command after connection opens
ws.on('open', () => {
  ws.send(JSON.stringify({"id": 1, "connect": {}}));
});
```

> **Note**: Passing custom headers in the WebSocket constructor only works with Node's `ws` library. Browser WebSocket APIs do not support custom headers. For browser environments, use the token-based method above.

---

## Message Format

All WebSocket messages follow a consistent JSON structure.

### Message Batching

The server may send multiple JSON objects in a single WebSocket frame, separated by newlines (NDJSON / newline-delimited JSON):

```
{"push":{"channel":"public.tickers.24h","pub":{"data":{...}}}}
{"push":{"channel":"public.kline.BTCYTEST.USD.1m","pub":{"data":{...}}}}
```

**Important**: Clients using `JSON.parse(message)` directly will throw `SyntaxError` on batched frames. Must split by newline and parse each line individually:

```javascript
function parseMessages(raw) {
  return raw.trim().split('\n')
    .filter(line => line.trim())
    .map(line => JSON.parse(line));
}
```

### Outbound Messages (Client to Server)
```json
{
  "id": 1,
  "method": "subscribe|unsubscribe",
  "params": {}
}
```

### Inbound Messages (Server to Client)

**Response Messages**:
```json
{
  "id": 1,
  "result": {},
  "error": null
}
```

**Push Notifications**:
```json
{
  "push": {
    "channel": "channel_name",
    "pub": {
      "data": {}
    }
  }
}
```

---

# Subscriptions

## Automatic Subscriptions (Authenticated Users Only)

Upon successful authentication, users are automatically subscribed to their private notification channels:

### Private Channel Name Format
- **Pattern**: `private.{wallet_address}`
- **Example**: `private.0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8`

---

## Private Subscription Types

### Order Updates

Real-time notifications for order state changes.

**Channel**: `private.{wallet_address}`

Order updates are delivered via two notification types:

#### `order.updated` — Order State Changes

Sent when an order is created, partially filled, or fully filled.

**Message Format**:

```json
{
  "push": {
    "channel": "private.0x5410Ac2b2f77D79Bd08bDd90eb67F3a5c09a4Beb",
    "pub": {
      "data": {
        "header": {
          "type": "order.updated",
          ...
        },
        "order_id": "5d418afd-d0d1-430a-b1b5-4fc98828ce32",
        "market": "BTCYTEST.USD",
        "side": "buy",
        "type": "limit",
        "state": "wait",
        ...
      }
    }
  }
}
```

**Data Fields**:
- `header`: Event metadata
- `order_id`: Order UUID (spot orders only)
- `channel_id`: Channel ID for the order
- `user_address`: User's wallet address
- `market`: Trading market (e.g., "BTCUSD")
- `side`: Order side (`buy` or `sell`)
- `type`: Order type (`limit`, `market`, etc.)
- `trigger_origin_type`: Original trigger order type (`stop_loss`, `stop_limit`, `take_profit`, `take_limit`) when the order was created by a trigger order. Omitted for non-trigger orders.
- `time_in_force`: Time in force (`gtc`, `ioc`, `fok`)
- `price`: Order price (execution price)
- `trigger_price`: Trigger price for trigger-originated orders. Omitted for non-trigger orders.
- `amount`: Current order amount
- `origin_amount`: Original order amount
- `state`: Order state (`pending`, `wait`, `filled`, `done`, `canceled`)
- `created_at`: Order creation timestamp
- `triggered_at`: Timestamp when the trigger condition was met (trigger orders only).
- `completed_at`: Timestamp when the order reached a final state (`done`, `canceled`, `rejected`, `expired`, including trigger variants). Omitted while the order is still open.

#### `order.cancelled` — Order Cancellation

Sent when an order is cancelled. This is a **separate event type** from `order.updated`.

> **Important**: Clients must listen for **both** `order.updated` and `order.cancelled` to receive all order notifications.

**Message Format**:
```json
{
  "push": {
    "channel": "private.0x5410Ac2b2f77D79Bd08bDd90eb67F3a5c09a4Beb",
    "pub": {
      "data": {
        "header": {
          "type": "order.cancelled"
        },
        "order_id": "5d418afd-d0d1-430a-b1b5-4fc98828ce32",
        "state": "canceled"
      }
    }
  }
}
```

**Data Fields**:
- `header.type`: `"order.cancelled"`
- `order_id`: Order UUID (spot orders)
- `state`: `"canceled"`

### Order Expiration

Notifications for expired orders.

**Channel**: `private.{wallet_address}`
**Notification Type**: `order.expired`

**Message Format**:
```json
{
  "push": {
    "channel": "private.0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
    "pub": {
      "data": {
        "header": {
          "id": "8b8eb153-1acd-4438-b5d2-6d8715b53bb2",
          "scope": "private",
          "type": "order.expired",
          "user_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
          "channel_id": "0xCAa253Db21906b86F17a8305C16DD7Db1D3f0000",
          "created_at": "2025-09-30T15:42:43.71880063Z"
        },
        "id": 222488,
        "uuid": "ad04afd7-e581-4f40-a038-edd14c58c54b",
        "channel_id": "0xCAa253Db21906b86F17a8305C16DD7Db1D3f0000",
        "user_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
        "market": "BTCUSD",
        "side": "buy",
        "type": "limit",
        "time_in_force": "fok",
        "price": "1",
        "amount": "4037.1",
        "origin_amount": "4037.1",
        "state": "expired",
        "created_at": "2025-09-30T15:42:43.71319921Z"
      }
    }
  }
}
```

### Spot Account State Updates

Real-time notifications for spot account state changes (account opened, closed, etc.).

**Channel**: `private.{wallet_address}`
**Notification Type**: `spot_account.state_update`

**Message Format**:
```json
{
  "push": {
    "channel": "private.0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
    "pub": {
      "data": {
        "header": {
          "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
          "scope": "private",
          "type": "spot_account.state_update",
          "user_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
          "created_at": "2025-10-24T12:00:00.000000000Z"
        },
        "account_id": "550e8400-e29b-41d4-a716-446655440000",
        "owner_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
        "app_session_id": "clearnet-session-123",
        "state": "open"
      }
    }
  }
}
```

**Data Fields**:
- `header`: Event metadata
- `account_id`: Unique spot account identifier
- `owner_address`: Wallet address of account owner
- `app_session_id`: Associated app session ID (e.g., ClearNet session)
- `state`: Account state (`open`, `closed`)

### Spot Balance Updates

Real-time updates on spot account balance changes for specific assets.

**Channel**: `private.{wallet_address}`
**Notification Type**: `spot_account.balance_update`

**Message Format**:
```json
{
  "push": {
    "channel": "private.0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
    "pub": {
      "data": {
        "header": {
          "id": "b1c2d3e4-f5a6-7890-bcde-f12345678901",
          "scope": "private",
          "type": "spot_account.balance_update",
          "user_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
          "created_at": "2025-10-24T12:00:00.000000000Z"
        },
        "app_session_id": "clearnet-session-123",
        "owner_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
        "asset_symbol": "USDT",
        "total_balance": "10000.5000",
        "available_balance": "9500.5000",
        "locked_balance": "500.0000",
        "last_updated": "2025-10-24T12:00:00.000000000Z"
      }
    }
  }
}
```

**Data Fields**:
- `header`: Event metadata
- `app_session_id`: Associated app session ID
- `owner_address`: Wallet address of account owner
- `asset_symbol`: Asset symbol (e.g., "USDT", "BTC", "ETH")
- `total_balance`: Total balance for this asset
- `available_balance`: Available balance for trading/withdrawal
- `locked_balance`: Balance locked in orders or pending operations
- `last_updated`: Timestamp of last balance update

### Spot Funds Deposited

Notifications when funds are successfully deposited into a spot account.

**Channel**: `private.{wallet_address}`
**Notification Type**: `spot_account.funds_deposited`

**Message Format**:
```json
{
  "push": {
    "channel": "private.0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
    "pub": {
      "data": {
        "header": {
          "id": "c1d2e3f4-a5b6-7890-cdef-123456789012",
          "scope": "private",
          "type": "spot_account.funds_deposited",
          "user_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
          "created_at": "2025-10-24T12:00:00.000000000Z"
        },
        "app_session_id": "clearnet-session-123",
        "owner_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
        "asset_symbol": "USDT",
        "amount": "1000.0000",
        "transaction_hash": "clearnet-session-123_12345",
        "deposited_at": "2025-10-24T12:00:00.000000000Z"
      }
    }
  }
}
```

**Data Fields**:
- `header`: Event metadata
- `app_session_id`: Associated app session ID
- `owner_address`: Wallet address of account owner
- `asset_symbol`: Deposited asset symbol
- `amount`: Deposit amount
- `transaction_hash`: Transaction hash or reference (optional)
- `deposited_at`: Timestamp when deposit was completed

### Spot Withdrawal Accepted

Notifications when a withdrawal request is accepted and funds are reserved.

**Channel**: `private.{wallet_address}`
**Notification Type**: `spot_account.withdrawal_accepted`

**Message Format**:
```json
{
  "push": {
    "channel": "private.0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
    "pub": {
      "data": {
        "header": {
          "id": "d1e2f3a4-b5c6-7890-def1-234567890123",
          "scope": "private",
          "type": "spot_account.withdrawal_accepted",
          "user_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
          "created_at": "2025-10-24T12:00:00.000000000Z"
        },
        "withdrawal_id": "withdrawal-550e8400-e29b-41d4-a716",
        "app_session_id": "clearnet-session-123",
        "owner_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
        "asset_symbol": "USDT",
        "amount": "500.0000",
        "requested_at": "2025-10-24T12:00:00.000000000Z"
      }
    }
  }
}
```

**Data Fields**:
- `header`: Event metadata
- `withdrawal_id`: Unique withdrawal identifier
- `app_session_id`: Associated app session ID
- `owner_address`: Wallet address of account owner
- `asset_symbol`: Asset being withdrawn
- `amount`: Withdrawal amount
- `requested_at`: Timestamp when withdrawal was requested

### Spot Withdrawal Completed

Notifications when a withdrawal is successfully completed.

**Channel**: `private.{wallet_address}`
**Notification Type**: `spot_account.withdrawal_completed`

**Message Format**:
```json
{
  "push": {
    "channel": "private.0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
    "pub": {
      "data": {
        "header": {
          "id": "e1f2a3b4-c5d6-7890-ef12-345678901234",
          "scope": "private",
          "type": "spot_account.withdrawal_completed",
          "user_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
          "created_at": "2025-10-24T12:05:00.000000000Z"
        },
        "withdrawal_id": "withdrawal-550e8400-e29b-41d4-a716",
        "app_session_id": "clearnet-session-123",
        "owner_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
        "asset_symbol": "USDT",
        "amount": "500.0000",
        "transaction_hash": "clearnet-session-123_12346",
        "completed_at": "2025-10-24T12:05:00.000000000Z"
      }
    }
  }
}
```

**Data Fields**:
- `header`: Event metadata
- `withdrawal_id`: Unique withdrawal identifier
- `app_session_id`: Associated app session ID
- `owner_address`: Wallet address of account owner
- `asset_symbol`: Asset that was withdrawn
- `amount`: Withdrawal amount
- `transaction_hash`: Transaction hash for the withdrawal
- `completed_at`: Timestamp when withdrawal was completed

### Spot Withdrawal Failed

Notifications when a withdrawal attempt fails and funds are returned to available balance.

**Channel**: `private.{wallet_address}`
**Notification Type**: `spot_account.withdrawal_failed`

**Message Format**:
```json
{
  "push": {
    "channel": "private.0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
    "pub": {
      "data": {
        "header": {
          "id": "f1a2b3c4-d5e6-7890-f123-456789012345",
          "scope": "private",
          "type": "spot_account.withdrawal_failed",
          "user_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
          "created_at": "2025-10-24T12:05:00.000000000Z"
        },
        "withdrawal_id": "withdrawal-550e8400-e29b-41d4-a716",
        "app_session_id": "clearnet-session-123",
        "owner_address": "0xCAa253Db21906b86F17a8305C16DD7Db1D3fc5b8",
        "asset_symbol": "USDT",
        "amount": "500.0000",
        "failure_reason": "Insufficient liquidity in destination",
        "failed_at": "2025-10-24T12:05:00.000000000Z"
      }
    }
  }
}
```

**Data Fields**:
- `header`: Event metadata
- `withdrawal_id`: Unique withdrawal identifier
- `app_session_id`: Associated app session ID
- `owner_address`: Wallet address of account owner
- `asset_symbol`: Asset that was attempted
- `amount`: Withdrawal amount that failed
- `failure_reason`: Reason for withdrawal failure
- `failed_at`: Timestamp when withdrawal failed

---

## Public Subscription Types

Public subscriptions are available to all users (authenticated and unauthenticated) and provide market data streams.

### Mark Price Updates

Subscribe to real-time mark price updates for a specific market.

**Subscription Request**:
```json
{
  "id": 2,
  "subscribe": {
    "channel": "public.mark_price.BTCUSD"
  }
}
```

**Success Response**:
```json
{
  "id": 2,
  "subscribe": {}
}
```

**Push Notifications**:
```json
{
  "push": {
    "channel": "public.mark_price.BTCUSD",
    "pub": {
      "data": {
        "header": {
          "id": "960fcdd0-6f0b-46a5-9647-6297093e168a",
          "scope": "public",
          "type": "mark_price",
          "created_at": "2025-09-30T14:42:52.153092769Z"
        },
        "market": "BTCUSD",
        "mark_price": "113143.17314493"
      }
    }
  }
}
```

**Channel Pattern**: `public.mark_price.{MARKET}`
**Available Markets**: `BTCUSD`, `ETHUSD`, etc.

### Orderbook Incremental Updates

Subscribe to real-time orderbook changes with incremental updates.

**Subscription Request**:
```json
{
  "id": 3,
  "subscribe": {
    "channel": "public.orderbook.increment.BTCUSD"
  }
}
```

**Initial Snapshot Response**:
```json
{
  "id": 3,
  "subscribe": {
    "data": {
      "header": {
        "id": "b626d90c-dc5d-4be7-9021-de265ea6764c",
        "scope": "public",
        "type": "orderbook.snapshot",
        "created_at": "2025-09-30T14:42:51.949396721Z"
      },
      "market": "BTCUSD",
      "sequence_num": 1436308,
      "bids": [
        ["113150", "0.15741658"],
        ["113140", "0.08147461"]
      ],
      "asks": [
        ["113170", "0.23884178"],
        ["113180", "0.21656283"]
      ]
    }
  }
}
```

**Incremental Updates**:
```json
{
  "push": {
    "channel": "public.orderbook.increment.BTCUSD",
    "pub": {
      "data": {
        "header": {
          "id": "5ee5f313-e5f3-4392-b167-a485481acd6a",
          "scope": "public",
          "type": "orderbook.increment",
          "created_at": "2025-09-30T14:42:52.009378122Z"
        },
        "market": "BTCUSD",
        "sequence_num": 1436309,
        "bids": [
          ["113140", "0.23295975"]
        ],
        "asks": []
      }
    }
  }
}
```

**Data Fields**:
- `market`: Trading market symbol
- `sequence_num`: Sequence number for ordering updates
- `bids`: Array of bid levels [price, amount] (empty array means no changes)
- `asks`: Array of ask levels [price, amount] (empty array means no changes)

**Channel Pattern**: `public.orderbook.increment.{MARKET}`

**Update Frequency and Aggregation**:

The orderbook increment stream implements intelligent aggregation to optimize client performance:

- **Aggregation Window**: Updates are aggregated within a configurable time window (default: 20ms)
- **Price Level Deduplication**: Multiple updates to the same price level within a window are merged, sending only the final state
- **Update Frequency**: Clients receive updates approximately 50 times per second (50 fps), providing smooth UI updates while reducing processing load
- **Sequence Numbers**: Sequence numbers remain monotonically increasing to ensure proper ordering

**Performance Benefits**:
- **Reduced Message Rate**: 80-95% fewer WebSocket messages compared to raw updates during active trading
- **Lower CPU Usage**: Frontend processes 50 updates/second instead of 500-1000+ updates/second
- **Smoother UI**: Update frequency matches typical screen refresh rates (60 fps)
- **Bandwidth Efficiency**: Larger aggregated messages compress better and have lower per-message overhead

**Behavior Details**:
- When a price level changes multiple times within the aggregation window, only the final state is sent
- If a price level is added then removed within the same window, it may not appear in updates
- Latency impact is minimal (<20ms) and acceptable for most trading scenarios
- Empty updates (no changes) are not sent

**Example Aggregation**:
```
Within 20ms window:
  - Price 50000: 0.5 BTC → 0.3 BTC → 0.8 BTC → 0.2 BTC
  - Price 50001: 1.0 BTC → deleted

Sent to client as single update:
  - Price 50000: 0.2 BTC (final state)
  - Price 50001: 0.0 BTC (deleted)
```

This approach ensures clients always maintain an accurate orderbook state while significantly reducing the computational overhead of processing high-frequency updates.

### Trade Stream (Snapshot + Increment)

Subscribe to aggregated trade executions for a specific market. Every subscription receives a configurable snapshot followed by incremental updates that guarantee sequence continuity.

**Subscription Request**:
```json
{
  "id": 7,
  "subscribe": {
    "channel": "public.trades.increment.BTC-USDT"
  }
}
```

**Snapshot Response** (sent immediately after a successful subscribe):
```json
{
  "id": 7,
  "subscribe": {
    "data": {
      "header": {
        "id": "37521ebc-38fc-4498-83df-e7ffe40f2297",
        "scope": "public",
        "type": "trades.snapshot",
        "created_at": "2025-11-24T02:45:36.441934775Z"
      },
      "market": "BTC-USDT",
      "sequence_num": 10,
      "trades": [
        {
          "id": 123455,
          "price": "67999.80",
          "amount": "0.0100",
          "direction": "sell",
          "executed_at": "2025-11-24T02:45:36.341934775Z"
        },
        {
          "id": 123456,
          "price": "68000.25",
          "amount": "0.0025",
          "direction": "buy",
          "executed_at": "2025-11-24T02:45:36.441934775Z"
        }
      ]
    }
  }
}
```

**Incremental Updates**:
```json
{
  "push": {
    "channel": "public.trades.increment.BTC-USDT",
    "pub": {
      "data": {
        "header": {
          "id": "2a5084e6-43d0-4a9a-8d7d-95a83827d9c6",
          "scope": "public",
          "type": "trades.increment",
          "created_at": "2025-11-24T02:45:36.491934775Z"
        },
        "market": "BTC-USDT",
        "sequence_num": 11,
        "trades": [
          {
            "id": 123457,
            "price": "68001.10",
            "amount": "0.0150",
            "direction": "buy",
            "executed_at": "2025-11-24T02:45:36.481934775Z"
          }
        ]
      }
    }
  }
}
```

**Channel Pattern**: `public.trades.increment.{MARKET}`

**Data Fields**:
- `market`: Market symbol (e.g., `BTC-USDT`)
- `sequence_num`: Monotonically increasing sequence number used to guarantee ordering
- `trades`: Array of aggregated trade entries
  - `id`: Monotonic identifier inside the aggregation window, use the first trade_id
  - `price`: Executed price (string for precision)
  - `amount`: Executed amount (string)
  - `direction`: Side of the taker order (`buy` or `sell`)
  - `executed_at`: ISO8601 timestamp with microsecond precision

**Behavior & Guarantees**:
- Every subscription starts with a snapshot whose `sequence_num` matches the most recent increment.
- Sequence numbers are contiguous. If a gap or duplicate is detected internally, the server automatically rebuilds the snapshot and broadcasts it before sending further increments.
- Clients should treat any missing sequence number as invalid and resubscribe to refresh state.
- Snapshot depth (number of recent trades) is configurable (`trade_stream.snapshot_size`), defaulting to 50 aggregated entries per market.

### 24-Hour Ticker Data

Subscribe to 24-hour ticker statistics for all markets.

**Subscription Request**:
```json
{
  "id": 4,
  "subscribe": {
    "channel": "public.tickers.24h"
  }
}
```

**Push Notifications**:
```json
{
  "push": {
    "channel": "public.tickers.24h",
    "pub": {
      "data": {
        "header": {
          "id": "75591ea1-0887-4b3d-8a5d-24400b3fb2ca",
          "scope": "public",
          "type": "tickers",
          "created_at": "2025-09-30T15:37:27.92730374Z"
        },
        "tickers": [
          {
            "market": "BTCUSD",
            "time": 1759246647916,
            "min": "113404.5",
            "max": "114377.2",
            "first": "113924.9",
            "last": "114288.3",
            "volume": "31003.27754213",
            "quoteVolume": "3534745005.83033429",
            "vwap": "114011.9782828447619754",
            "priceChange": "+0.32%"
          },
          {
            "market": "ETHUSD",
            "time": 1759246647924,
            "min": "0",
            "max": "0",
            "first": "0",
            "last": "0",
            "volume": "0",
            "quoteVolume": "0",
            "vwap": "0",
            "priceChange": "+0.00%"
          }
        ]
      }
    }
  }
}
```

**Ticker Data Fields**:
- `market`: Market symbol
- `time`: Timestamp of the data
- `min`: 24h lowest price
- `max`: 24h highest price
- `first`: 24h opening price
- `last`: Current/last price
- `volume`: 24h base volume
- `quoteVolume`: 24h quote volume
- `vwap`: Volume-weighted average price
- `priceChange`: 24h price change percentage

**Channel**: `public.tickers.24h`

### Kline/Candlestick Data

Subscribe to real-time kline (candlestick) data for specific market and interval.

**Subscription Request**:
```json
{
  "id": 6,
  "subscribe": {
    "channel": "public.kline.BTCUSD.15m"
  }
}
```

**Push Notifications**:
```json
{
  "push": {
    "channel": "public.kline.BTCUSD.15m",
    "pub": {
      "data": {
        "header": {
          "id": "1b7a62eb-5f54-4492-a9c4-a7144335d183",
          "scope": "public",
          "type": "kline",
          "created_at": "2025-09-30T14:42:56.947631975Z"
        },
        "market": "BTCUSD",
        "interval": "15m",
        "kline": [
          1759242600000,
          "113044.8",
          "113225.5",
          "112978.9",
          "113137.7",
          "925.77107192",
          39456
        ]
      }
    }
  }
}
```

**Kline Array Format**:
- `[0]`: Open time (milliseconds)
- `[1]`: Open price
- `[2]`: High price
- `[3]`: Low price
- `[4]`: Close price
- `[5]`: Volume
- `[6]`: Number of trades

**Channel Pattern**: `public.kline.{MARKET}.{INTERVAL}`
**Available Intervals**: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`, `3d`, `1w`, `1M`

---

## RPC Methods (Authenticated Only)

RPC (Remote Procedure Call) methods allow authenticated users to perform trading operations and query account information through the WebSocket connection.

### General RPC Format

**Request**:
```json
{
  "id": 100,
  "rpc": {
    "method": "method_name",
    "params": {}
  }
}
```

**Success Response**:
```json
{
  "id": 100,
  "rpc": {
    "result": {}
  }
}
```

**Error Response**:
```json
{
  "id": 100,
  "rpc": {
    "error": {
      "code": 400,
      "message": "Error description"
    }
  }
}
```

### Available RPC Methods

- `get_margin_account_positions`: Retrieve current positions for a margin account
- `get_margin_account_info`: Get margin account balance and information
- `post_create_order`: Create new trading orders

*Note: Detailed RPC method documentation will be added based on actual implementation discovery.*

---

## Subscription Management

> **Important**: Each subsequent subscribe/unsubscribe message must use an incrementing `id` value (n+1). The `id` field helps track request-response pairs and must be unique for each message sent.

### Subscribe to Channel

**Request**:
```json
{
  "id": 1,
  "subscribe": {
    "channel": "channel_name"
  }
}
```

**Success Response**:
```json
{
  "id": 1,
  "subscribe": {}
}
```

**Error Response**:
```json
{
  "id": 1,
  "subscribe": {
    "error": {
      "code": 400,
      "message": "Invalid channel name"
    }
  }
}
```

### Unsubscribe from Channel

**Request**:
```json
{
  "id": 2,
  "unsubscribe": {
    "channel": "channel_name"
  }
}
```

**Success Response**:
```json
{
  "id": 2,
  "unsubscribe": {}
}
```

---

## Connection Management

### Ping/Pong

The server sends periodic ping frames to maintain connection health. Clients should respond with pong frames.

**Configuration**:
- Ping interval: 5 minutes
- Pong timeout: 5 minutes

### Reconnection

The client should implement automatic reconnection with exponential backoff:
- Initial reconnection delay: 1 second
- Maximum reconnection delay: 60 seconds
- Backoff multiplier: 2

### Connection Recovery

The WebSocket API supports connection recovery features:
- **Message Positioning**: Clients can resume from the last received message
- **Automatic Resubscription**: Previous subscriptions are restored after reconnection
- **State Synchronization**: Account state is synchronized upon reconnection

---

## Error Handling

### Common Error Codes

**Authentication Errors**:
- `401` - Authentication failed or token invalid
- `403` - Insufficient permissions for requested operation

**Subscription Errors**:
- `400` - Invalid channel name or subscription parameters
- `404` - Channel not found or not available
- `409` - Already subscribed to channel

**RPC Errors**:
- `400` - Invalid RPC method or parameters
- `405` - RPC method not allowed for current user
- `500` - Internal server error during RPC execution

### Error Message Format

All error responses include structured error information:

```json
{
  "error": {
    "code": 400,
    "message": "Human-readable error description",
    "details": {
      "field": "additional_error_context"
    }
  }
}
```

---

## Rate Limiting

WebSocket connections are subject to rate limiting to ensure fair usage:

- **Connection Limit**: 5 concurrent connections per user
- **Subscription Limit**: 100 active subscriptions per connection
- **RPC Rate Limit**: 10 RPC calls per second per connection
- **Message Rate Limit**: 100 messages per second per connection

Rate limit headers are not applicable to WebSocket connections. Rate limiting violations result in connection termination.

---

## Data Types

### Decimal Precision
All monetary and quantity values are represented as strings to maintain precision:
```json
{
  "price": "35000.50000000",
  "amount": "1.50000000"
}
```

### Timestamps
Timestamps are provided in various formats:
- **ISO 8601**: `"2025-09-30T15:40:54.455631184Z"` (in headers)
- **Unix Milliseconds**: `1759246647916` (in ticker data)

### UUIDs
Event and order UUIDs follow standard UUID v4 format:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Channel Names
Channel names follow consistent patterns:
- **Private**: `private.{wallet_address}`
- **Public Mark Price**: `public.mark_price.{MARKET}`
- **Public Orderbook**: `public.orderbook.increment.{MARKET}`
- **Public Trades**: `public.trades.increment.{MARKET}`
- **Public Klines**: `public.kline.{MARKET}.{INTERVAL}`
- **Public Tickers**: `public.tickers.24h`
