import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_message import OrderBookMessage, OrderBookMessageType


def _parse_float_book(values: List[List[Any]]) -> List[List[float]]:
    return [[float(price), float(amount)] for price, amount in values]


def _parse_iso_timestamp(value: Optional[str]) -> float:
    if not value:
        return time.time()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


class YellowProOrderBook(OrderBook):

    @classmethod
    def snapshot_message_from_exchange(
            cls,
            msg: Dict[str, Any],
            timestamp: float,
            metadata: Optional[Dict[str, Any]] = None) -> OrderBookMessage:
        if metadata:
            msg.update(metadata)
        bids = _parse_float_book(msg.get("bids", []))
        asks = _parse_float_book(msg.get("asks", []))
        update_id = int(msg.get("sequence_num") or int(timestamp * 1e3))
        return OrderBookMessage(
            OrderBookMessageType.SNAPSHOT,
            {
                "trading_pair": msg["trading_pair"],
                "update_id": update_id,
                "bids": bids,
                "asks": asks,
            },
            timestamp=timestamp,
        )

    @classmethod
    def diff_message_from_exchange(
            cls,
            msg: Dict[str, Any],
            timestamp: Optional[float] = None,
            metadata: Optional[Dict[str, Any]] = None) -> OrderBookMessage:
        if metadata:
            msg.update(metadata)
        bids = _parse_float_book(msg.get("bids", []))
        asks = _parse_float_book(msg.get("asks", []))
        update_id = int(msg.get("sequence_num"))
        ts = timestamp or _parse_iso_timestamp(msg.get("created_at") or msg.get("timestamp"))
        return OrderBookMessage(
            OrderBookMessageType.DIFF,
            {
                "trading_pair": msg["trading_pair"],
                "update_id": update_id,
                "bids": bids,
                "asks": asks,
            },
            timestamp=ts,
        )

    @classmethod
    def trade_message_from_exchange(
            cls,
            msg: Dict[str, Any],
            metadata: Optional[Dict[str, Any]] = None) -> OrderBookMessage:
        if metadata:
            msg.update(metadata)
        side = msg.get("side", "").lower()
        trade_type = TradeType.BUY if side in ("buy", "bid", "b") else TradeType.SELL
        trade_id = str(msg.get("id") or msg.get("trade_id") or msg.get("uuid") or "")
        timestamp = msg.get("executed_at") or msg.get("timestamp") or msg.get("created_at")
        price = float(msg.get("price"))
        amount = float(msg.get("amount"))
        return OrderBookMessage(
            OrderBookMessageType.TRADE,
            {
                "trading_pair": msg["trading_pair"],
                "trade_type": float(trade_type.value),
                "trade_id": trade_id,
                "price": price,
                "amount": amount,
            },
            timestamp=_parse_iso_timestamp(timestamp),
        )
