import asyncio
from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from bidict import bidict

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS
from hummingbot.connector.exchange.yellow_pro.yellow_pro_api_order_book_data_source import YellowProAPIOrderBookDataSource
from hummingbot.connector.exchange.yellow_pro.yellow_pro_exchange import YellowProExchange
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_message import OrderBookMessageType


class YellowProAPIOrderBookDataSourceTests(IsolatedAsyncioWrapperTestCase):
    level = 0

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.base_asset = "COINALPHA"
        cls.quote_asset = "HBOT"
        cls.trading_pair = f"{cls.base_asset}-{cls.quote_asset}"
        cls.exchange_symbol = f"{cls.base_asset}{cls.quote_asset}"

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.log_records = []
        self.listening_task = None

        self.connector = YellowProExchange(
            yellow_pro_app_session_id="app-session",
            trading_pairs=[self.trading_pair],
            trading_required=False,
        )
        self.connector._auth_instance = None
        self.connector._set_trading_pair_symbol_map(bidict({self.exchange_symbol: self.trading_pair}))

        self.data_source = YellowProAPIOrderBookDataSource(
            trading_pairs=[self.trading_pair],
            connector=self.connector,
            api_factory=self.connector._web_assistants_factory,
            domain=CONSTANTS.DOMAIN,
        )
        self.data_source.logger().setLevel(1)
        self.data_source.logger().addHandler(self)

        self._original_full_order_book_reset_time = self.data_source.FULL_ORDER_BOOK_RESET_DELTA_SECONDS
        self.data_source.FULL_ORDER_BOOK_RESET_DELTA_SECONDS = -1

    def tearDown(self) -> None:
        self.listening_task and self.listening_task.cancel()
        self.data_source.FULL_ORDER_BOOK_RESET_DELTA_SECONDS = self._original_full_order_book_reset_time
        super().tearDown()

    def handle(self, record):
        self.log_records.append(record)

    def _is_logged(self, level: str, message: str) -> bool:
        return any(record.levelname == level and record.getMessage() == message for record in self.log_records)

    def _snapshot_rest_payload(self):
        return {
            "sequence_num": "1027024",
            "bids": [["100.0", "1.0"]],
            "asks": [["101.0", "2.0"]],
        }

    def _diff_push_event(self):
        return {
            "push": {
                "channel": f"public.orderbook.increment.{self.exchange_symbol}",
                "pub": {
                    "data": {
                        "market": self.exchange_symbol,
                        "sequence_num": 2002,
                        "header": {"created_at": "2024-05-05T00:00:00Z"},
                        "bids": [["99.5", "1.5"]],
                        "asks": [["101.5", "2.5"]],
                    }
                },
            }
        }

    def _snapshot_subscribe_event(self):
        return {
            "subscribe": {
                "data": {
                    "market": self.exchange_symbol,
                    "sequence_num": 1500,
                    "header": {"created_at": "2024-05-05T00:15:00Z"},
                    "bids": [["100.1", "5"]],
                    "asks": [["101.1", "4"]],
                }
            }
        }

    def _trade_push_event(self):
        return {
            "push": {
                "channel": f"public.trades.{self.exchange_symbol}",
                "pub": {
                    "data": {
                        "market": self.exchange_symbol,
                        "trade_id": "12345",
                        "price": "101.2",
                        "amount": "3.5",
                        "side": "buy",
                        "executed_at": "2024-05-05T00:30:00Z",
                    }
                },
            }
        }

    async def test_get_last_traded_prices_delegates_to_connector(self):
        expected = {self.trading_pair: 123.45}
        self.connector.get_last_traded_prices = AsyncMock(return_value=expected)

        result = await self.data_source.get_last_traded_prices([self.trading_pair])

        self.connector.get_last_traded_prices.assert_awaited_once_with(trading_pairs=[self.trading_pair])
        self.assertEqual(expected, result)

    async def test_request_order_book_snapshot_fetches_snapshot(self):
        rest_payload = {"sequence_num": "42"}
        self.connector._api_get = AsyncMock(return_value=rest_payload)

        result = await self.data_source._request_order_book_snapshot(self.trading_pair)

        self.connector._api_get.assert_awaited_once_with(
            path_url=CONSTANTS.SNAPSHOT_REST_URL,
            params={"symbol": self.exchange_symbol},
            limit_id=CONSTANTS.SNAPSHOT_REST_URL,
            is_auth_required=True,
        )
        self.assertEqual(self.trading_pair, result["trading_pair"])
        self.assertEqual(42, result["sequence_num"])
        self.assertEqual([], result["bids"])
        self.assertEqual([], result["asks"])

    async def test_order_book_snapshot_returns_message(self):
        snapshot_payload = self._snapshot_rest_payload()
        snapshot_payload["trading_pair"] = self.trading_pair
        with patch.object(
            self.data_source, "_request_order_book_snapshot", AsyncMock(return_value=snapshot_payload)
        ), patch.object(self.data_source, "_time", return_value=123456789.0):
            message = await self.data_source._order_book_snapshot(self.trading_pair)

        self.assertEqual(OrderBookMessageType.SNAPSHOT, message.type)
        self.assertEqual(1027024, message.update_id)
        self.assertEqual(self.trading_pair, message.trading_pair)

    async def test_get_new_order_book_applies_snapshot(self):
        snapshot_payload = self._snapshot_rest_payload()
        snapshot_payload["trading_pair"] = self.trading_pair
        with patch.object(
            self.data_source, "_request_order_book_snapshot", AsyncMock(return_value=snapshot_payload)
        ), patch.object(self.data_source, "_time", return_value=1000):
            order_book: OrderBook = await self.data_source.get_new_order_book(self.trading_pair)

        bids = list(order_book.bid_entries())
        asks = list(order_book.ask_entries())
        self.assertEqual(1, len(bids))
        self.assertEqual(1, len(asks))
        self.assertEqual(100.0, bids[0].price)
        self.assertEqual(101.0, asks[0].price)

    async def test_subscribe_channels_without_token(self):
        ws_mock = MagicMock()
        ws_mock.send = AsyncMock()

        await self.data_source._subscribe_channels(ws_mock)

        self.assertEqual(2, ws_mock.send.call_count)
        connect_payload = ws_mock.send.call_args_list[0].args[0].payload
        self.assertEqual({"id": 1, "connect": {}}, connect_payload)
        subscribe_payload = ws_mock.send.call_args_list[1].args[0].payload
        self.assertEqual(
            f"public.orderbook.increment.{self.exchange_symbol}",
            subscribe_payload["subscribe"]["channel"],
        )

    async def test_subscribe_channels_includes_token(self):
        token = "token-123"
        auth_mock = MagicMock()
        auth_mock.ensure_active_token = AsyncMock(return_value=token)
        self.connector._auth_instance = auth_mock
        ws_mock = MagicMock()
        ws_mock.send = AsyncMock()

        await self.data_source._subscribe_channels(ws_mock)

        connect_payload = ws_mock.send.call_args_list[0].args[0].payload
        self.assertEqual(token, connect_payload["connect"]["token"])

    async def test_subscribe_channels_raises_cancelled_error(self):
        ws_mock = MagicMock()
        ws_mock.send = AsyncMock(side_effect=asyncio.CancelledError())

        with self.assertRaises(asyncio.CancelledError):
            await self.data_source._subscribe_channels(ws_mock)

    async def test_subscribe_channels_logs_on_generic_error(self):
        ws_mock = MagicMock()
        ws_mock.send = AsyncMock(side_effect=Exception("boom"))

        with self.assertRaises(Exception):
            await self.data_source._subscribe_channels(ws_mock)

        self.assertTrue(
            self._is_logged("ERROR", "Unexpected error occurred subscribing to YellowPro order book channels.")
        )

    async def test_listen_for_subscriptions_subscribes_to_channels(self):
        ws_mock = MagicMock()
        ws_mock.disconnect = AsyncMock()
        self.data_source._connected_websocket_assistant = AsyncMock(
            side_effect=[ws_mock, asyncio.CancelledError()]
        )
        self.data_source._subscribe_channels = AsyncMock()
        self.data_source._process_websocket_messages = AsyncMock(side_effect=asyncio.CancelledError)

        with self.assertRaises(asyncio.CancelledError):
            await self.data_source.listen_for_subscriptions()

        self.data_source._subscribe_channels.assert_awaited_once_with(ws_mock)
        self.data_source._process_websocket_messages.assert_awaited_once_with(websocket_assistant=ws_mock)
        ws_mock.disconnect.assert_awaited_once()
        self.assertTrue(self._is_logged("INFO", "Subscribed to YellowPro order book channels."))

    async def test_listen_for_subscriptions_logs_connection_error(self):
        ws_mock = MagicMock()
        ws_mock.disconnect = AsyncMock()
        self.data_source._connected_websocket_assistant = AsyncMock(
            side_effect=[ws_mock, asyncio.CancelledError()]
        )
        self.data_source._subscribe_channels = AsyncMock()
        self.data_source._process_websocket_messages = AsyncMock(side_effect=ConnectionError("Closed"))

        with self.assertRaises(asyncio.CancelledError):
            await self.data_source.listen_for_subscriptions()

        self.assertTrue(self._is_logged("WARNING", "The websocket connection was closed (Closed)"))

    async def test_listen_for_subscriptions_logs_generic_error_and_sleeps(self):
        ws_mock = MagicMock()
        ws_mock.disconnect = AsyncMock()
        self.data_source._connected_websocket_assistant = AsyncMock(
            side_effect=[ws_mock, asyncio.CancelledError()]
        )
        self.data_source._subscribe_channels = AsyncMock()
        self.data_source._process_websocket_messages = AsyncMock(side_effect=Exception("boom"))
        self.data_source._sleep = AsyncMock()

        with self.assertRaises(asyncio.CancelledError):
            await self.data_source.listen_for_subscriptions()

        self.assertTrue(
            self._is_logged(
                "ERROR",
                "Unexpected error occurred when listening to YellowPro order book streams. Retrying in 5 seconds...",
            )
        )
        self.data_source._sleep.assert_awaited_once_with(5.0)

    async def test_parse_order_book_diff_message_adds_message_to_queue(self):
        queue = asyncio.Queue()
        await self.data_source._parse_order_book_diff_message(self._diff_push_event(), queue)

        message = await queue.get()
        self.assertEqual(OrderBookMessageType.DIFF, message.type)
        self.assertEqual(2002, message.update_id)
        self.assertEqual(self.trading_pair, message.trading_pair)

    async def test_parse_order_book_snapshot_message_adds_message_to_queue(self):
        queue = asyncio.Queue()
        await self.data_source._parse_order_book_snapshot_message(self._snapshot_subscribe_event(), queue)

        message = await queue.get()
        self.assertEqual(OrderBookMessageType.SNAPSHOT, message.type)
        self.assertEqual(1500, message.update_id)
        self.assertEqual(self.trading_pair, message.trading_pair)

    async def test_parse_trade_message_adds_message_to_queue(self):
        queue = asyncio.Queue()
        await self.data_source._parse_trade_message(self._trade_push_event(), queue)

        message = await queue.get()
        self.assertEqual(OrderBookMessageType.TRADE, message.type)
        self.assertEqual("12345", message.content["trade_id"])

    def test_channel_originating_message_routes_channels(self):
        snapshot_event = self._snapshot_subscribe_event()
        diff_event = self._diff_push_event()
        trade_event = self._trade_push_event()
        unknown_event = {"push": {"channel": "something.else"}}

        self.assertEqual(
            self.data_source._snapshot_messages_queue_key,
            self.data_source._channel_originating_message(snapshot_event),
        )
        self.assertEqual(
            self.data_source._diff_messages_queue_key,
            self.data_source._channel_originating_message(diff_event),
        )
        self.assertEqual(
            self.data_source._trade_messages_queue_key,
            self.data_source._channel_originating_message(trade_event),
        )
        self.assertEqual("", self.data_source._channel_originating_message(unknown_event))

    @patch(
        "hummingbot.connector.exchange.yellow_pro.yellow_pro_api_order_book_data_source.asyncio.sleep",
        side_effect=asyncio.CancelledError,
    )
    async def test_listen_for_trades_logs_debug_and_sleeps(self, sleep_mock):
        queue = asyncio.Queue()

        with self.assertRaises(asyncio.CancelledError):
            await self.data_source.listen_for_trades(self.local_event_loop, queue)

        self.assertTrue(
            self._is_logged("DEBUG", "YellowPro trade websocket stream not available; skipping trade listener.")
        )
        sleep_mock.assert_called_once()
