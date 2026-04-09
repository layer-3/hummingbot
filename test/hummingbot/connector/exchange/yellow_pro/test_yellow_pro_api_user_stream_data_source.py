import asyncio
from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase
from unittest.mock import AsyncMock, MagicMock

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS
from hummingbot.connector.exchange.yellow_pro.yellow_pro_api_user_stream_data_source import YellowProAPIUserStreamDataSource


class YellowProAPIUserStreamDataSourceTests(IsolatedAsyncioWrapperTestCase):
    level = 0

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.auth = MagicMock()
        self.auth.ensure_active_token = AsyncMock(return_value="token-123")
        self.connector = MagicMock()
        self.ws_assistant = MagicMock()
        self.ws_assistant.connect = AsyncMock()
        self.ws_assistant.send = AsyncMock()
        self.api_factory = MagicMock()
        self.api_factory.get_ws_assistant = AsyncMock(return_value=self.ws_assistant)

        self.data_source = YellowProAPIUserStreamDataSource(
            auth=self.auth,
            trading_pairs=["COINALPHA-HBOT"],
            connector=self.connector,
            api_factory=self.api_factory,
            domain=CONSTANTS.DOMAIN,
        )

    async def test_connected_websocket_assistant_attaches_authorization_header(self):
        ws = await self.data_source._connected_websocket_assistant()

        self.assertIs(ws, self.ws_assistant)
        self.ws_assistant.connect.assert_awaited_once()
        called_kwargs = self.ws_assistant.connect.await_args.kwargs
        self.assertEqual(
            f"Bearer token-123",
            called_kwargs["ws_headers"]["Authorization"],
        )

    async def test_subscribe_channels_sends_connect_payload_with_token(self):
        await self.data_source._subscribe_channels(self.ws_assistant)

        self.ws_assistant.send.assert_awaited_once()
        sent_payload = self.ws_assistant.send.await_args.args[0].payload
        self.assertEqual("token-123", sent_payload["connect"]["token"])

    def test_json_fragments_splits_multiple_objects(self):
        raw = '{"a": 1} {"b": 2}   [ {"c": 3} ]'

        fragments = self.data_source._json_fragments(raw)

        self.assertEqual([{"a": 1}, {"b": 2}, [{"c": 3}]], fragments)

    async def test_process_event_message_records_private_channels(self):
        queue = asyncio.Queue()
        message = {"connect": {"subs": {"private.orders": {}, "private.balance": {}}}}

        await self.data_source._process_event_message(message, queue)

        self.assertEqual(["private.orders", "private.balance"], self.data_source._private_channels)
        self.assertTrue(queue.empty())

    async def test_process_event_message_enqueues_push_payloads(self):
        queue = asyncio.Queue()
        message = {"push": {"channel": "private.trades"}}

        await self.data_source._process_event_message(message, queue)

        queued = queue.get_nowait()
        self.assertEqual(message, queued)

    async def test_process_event_message_raises_on_error_payload(self):
        queue = asyncio.Queue()
        message = {"error": {"code": 1, "msg": "boom"}}

        with self.assertRaises(IOError):
            await self.data_source._process_event_message(message, queue)

    async def test_process_event_message_handles_raw_string_batches(self):
        queue = asyncio.Queue()
        raw = '{"push": {"channel": "one"}} {"push": {"channel": "two"}}'

        await self.data_source._process_event_message(raw, queue)

        first = queue.get_nowait()
        second = queue.get_nowait()
        self.assertEqual({"push": {"channel": "one"}}, first)
        self.assertEqual({"push": {"channel": "two"}}, second)
