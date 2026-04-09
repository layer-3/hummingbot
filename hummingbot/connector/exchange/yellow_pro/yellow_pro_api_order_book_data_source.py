import asyncio
import itertools
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS, yellow_pro_web_utils as web_utils
from hummingbot.connector.exchange.yellow_pro.yellow_pro_order_book import YellowProOrderBook
from hummingbot.core.data_type.order_book_message import OrderBookMessage
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant

if TYPE_CHECKING:
    from hummingbot.connector.exchange.yellow_pro.yellow_pro_exchange import YellowProExchange


class YellowProAPIOrderBookDataSource(OrderBookTrackerDataSource):
    ONE_HOUR = 60 * 60

    def __init__(
            self,
            trading_pairs: List[str],
            connector: "YellowProExchange",
            api_factory: WebAssistantsFactory,
            domain: str = CONSTANTS.DOMAIN):
        super().__init__(trading_pairs)
        self._connector = connector
        self._api_factory = api_factory
        self._domain = domain
        self._subscription_id_iterator = itertools.count(start=1)
        self._trade_messages_queue_key = CONSTANTS.TRADE_EVENT_TYPE
        self._diff_messages_queue_key = CONSTANTS.DIFF_EVENT_TYPE
        self.order_book_create_function = lambda: YellowProOrderBook()

    async def get_last_traded_prices(self, trading_pairs: List[str], domain: Optional[str] = None) -> Dict[str, float]:
        return await self._connector.get_last_traded_prices(trading_pairs=trading_pairs)

    async def _request_order_book_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        exchange_symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair)
        params = {"symbol": exchange_symbol}
        snapshot = await self._connector._api_get(
            path_url=CONSTANTS.SNAPSHOT_REST_URL,
            params=params,
            limit_id=CONSTANTS.SNAPSHOT_REST_URL,
            is_auth_required=True,
        )
        snapshot = snapshot or {}
        snapshot.setdefault("bids", [])
        snapshot.setdefault("asks", [])
        snapshot["trading_pair"] = trading_pair
        snapshot["sequence_num"] = int(snapshot.get("sequence_num") or 0)
        return snapshot

    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        snapshot_dict = await self._request_order_book_snapshot(trading_pair)
        snapshot_timestamp: float = self._time()
        return YellowProOrderBook.snapshot_message_from_exchange(
            snapshot_dict,
            snapshot_timestamp,
            metadata={"trading_pair": trading_pair},
        )

    async def listen_for_subscriptions(self):
        ws: Optional[WSAssistant] = None
        while True:
            try:
                ws = await self._connected_websocket_assistant()
                await self._subscribe_channels(ws)
                self.logger().info("Subscribed to YellowPro order book channels.")
                await self._process_websocket_messages(websocket_assistant=ws)
            except asyncio.CancelledError:
                raise
            except ConnectionError as connection_exception:
                self.logger().warning(f"The websocket connection was closed ({connection_exception})")
            except Exception:
                self.logger().exception(
                    "Unexpected error occurred when listening to YellowPro order book streams. "
                    "Retrying in 5 seconds...",
                )
                await self._sleep(5.0)
            finally:
                await self._on_order_stream_interruption(websocket_assistant=ws)

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        await ws.connect(
            ws_url=web_utils.wss_url(self._domain),
            ping_timeout=CONSTANTS.HEARTBEAT_TIME_INTERVAL,
        )
        return ws

    async def _subscribe_channels(self, ws: WSAssistant):
        try:
            connect_payload: Dict[str, Any] = {
                "id": next(self._subscription_id_iterator),
                "connect": {},
            }
            connect_request = WSJSONRequest(payload=connect_payload)
            await ws.send(connect_request)

            for trading_pair in self._trading_pairs:
                exchange_symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair)
                payload = {
                    "id": next(self._subscription_id_iterator),
                    "subscribe": {
                        "channel": f"public.orderbook.increment.{exchange_symbol}",
                    },
                }
                subscribe_request = WSJSONRequest(payload=payload)
                await ws.send(subscribe_request)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().exception("Unexpected error occurred subscribing to YellowPro order book channels.")
            raise

    async def _parse_order_book_diff_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        if "push" not in raw_message:
            return
        payload = raw_message["push"]["pub"]["data"]
        exchange_symbol = payload.get("market")
        trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=exchange_symbol)
        header = payload.get("header", {})
        diff_payload = {
            "trading_pair": trading_pair,
            "bids": payload.get("bids", []),
            "asks": payload.get("asks", []),
            "sequence_num": payload["sequence_num"],
            "created_at": header.get("created_at"),
        }
        diff_msg = YellowProOrderBook.diff_message_from_exchange(diff_payload)
        message_queue.put_nowait(diff_msg)

    async def _parse_order_book_snapshot_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        subscribe_block = raw_message.get("subscribe", {})
        if "data" not in subscribe_block:
            return
        payload = subscribe_block["data"]
        header = payload.get("header", {})
        exchange_symbol = payload.get("market")
        trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=exchange_symbol)
        snapshot_payload = {
            "trading_pair": trading_pair,
            "bids": payload.get("bids", []),
            "asks": payload.get("asks", []),
            "sequence_num": payload.get("sequence_num") or 0,
            "created_at": header.get("created_at"),
        }
        snapshot_timestamp: float = self._time()
        snapshot_msg = YellowProOrderBook.snapshot_message_from_exchange(snapshot_payload, snapshot_timestamp)
        message_queue.put_nowait(snapshot_msg)

    async def _parse_trade_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        if "push" not in raw_message:
            return
        payload = raw_message["push"]["pub"]["data"]
        exchange_symbol = payload.get("market")
        trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=exchange_symbol)
        trade_payload = {
            "trading_pair": trading_pair,
            "id": payload.get("trade_id") or payload.get("id"),
            "price": payload.get("price"),
            "amount": payload.get("amount"),
            "side": payload.get("side"),
            "executed_at": payload.get("executed_at") or payload.get("timestamp"),
        }
        trade_msg = YellowProOrderBook.trade_message_from_exchange(trade_payload)
        message_queue.put_nowait(trade_msg)

    def _channel_originating_message(self, event_message: Dict[str, Any]) -> str:
        if not isinstance(event_message, dict):
            return ""

        subscribe_block = event_message.get("subscribe")
        if isinstance(subscribe_block, dict) and "data" in subscribe_block:
            return self._snapshot_messages_queue_key

        push_block = event_message.get("push")
        if isinstance(push_block, dict):
            channel = push_block.get("channel", "")
            if "orderbook.increment" in channel:
                return self._diff_messages_queue_key
            if "trades" in channel:
                return self._trade_messages_queue_key
        return ""

    async def listen_for_trades(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """
        YellowPro currently does not expose a dedicated public trades websocket channel (subscribe attempts respond with
        code 107). Override the default behaviour to avoid keeping an idle websocket consumer.
        """
        self.logger().debug("YellowPro trade websocket stream not available; skipping trade listener.")
        while True:
            await asyncio.sleep(self.ONE_HOUR)
