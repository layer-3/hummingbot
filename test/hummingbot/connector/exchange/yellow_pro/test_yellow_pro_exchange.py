import asyncio
from decimal import Decimal
from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from bidict import bidict

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS
from hummingbot.connector.exchange.yellow_pro.yellow_pro_exchange import YellowProExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState


class YellowProExchangeTests(IsolatedAsyncioWrapperTestCase):
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
        self.exchange = YellowProExchange(
            yellow_pro_app_session_id="session-id",
            trading_pairs=[self.trading_pair],
            trading_required=False,
        )
        self.exchange._set_trading_pair_symbol_map(bidict({self.exchange_symbol: self.trading_pair}))

    def test_name_and_client_order_properties(self):
        self.assertEqual(CONSTANTS.EXCHANGE_NAME, self.exchange.name)
        self.assertEqual(CONSTANTS.MAX_ORDER_ID_LEN, self.exchange.client_order_id_max_length)
        self.assertEqual(CONSTANTS.BROKER_ID, self.exchange.client_order_id_prefix)

    def test_decimal_to_str_strips_trailing_zeroes(self):
        self.assertEqual("1.23", self.exchange._decimal_to_str(Decimal("1.23000")))

    def test_normalize_bool_handles_multiple_input_types(self):
        self.assertTrue(self.exchange._normalize_bool("true"))
        self.assertTrue(self.exchange._normalize_bool("maker"))
        self.assertFalse(self.exchange._normalize_bool("0"))
        self.assertIsNone(self.exchange._normalize_bool("unknown"))

    def test_is_maker_from_payload_uses_channel_reference(self):
        self.exchange._channel_id = "channel-1"
        payload = {"maker_id": "channel-1"}

        self.assertTrue(self.exchange._is_maker_from_payload(payload))

    def test_orders_cache_key_includes_market_and_channel(self):
        self.exchange._channel_id = "Channel"
        key = self.exchange._orders_cache_key(self.exchange_symbol)

        self.assertEqual(f"{self.exchange_symbol}:channel", key)

    async def test_get_last_traded_prices_fetches_remote_price(self):
        price_payload = {"last": "101.5"}
        self.exchange._api_get = AsyncMock(return_value=price_payload)

        prices = await self.exchange.get_last_traded_prices([self.trading_pair])

        self.assertEqual({self.trading_pair: 101.5}, prices)
        self.exchange._api_get.assert_awaited_once()

    async def test_get_positions_uses_provided_channel_id(self):
        positions_payload = [{"channelID": "channel-1"}]
        self.exchange._api_get = AsyncMock(return_value=positions_payload)

        result = await self.exchange.get_positions("channel-1")

        self.assertEqual(positions_payload, result)
        kwargs = self.exchange._api_get.call_args.kwargs
        self.assertEqual(CONSTANTS.POSITIONS_URL, kwargs["path_url"])
        self.assertEqual({"channel_id": "channel-1"}, kwargs["params"])
        self.assertTrue(kwargs["is_auth_required"])

    async def test_get_positions_defaults_to_configured_channel(self):
        positions_payload = {"positions": [{"channelID": "configured"}]}
        self.exchange._api_get = AsyncMock(return_value=positions_payload)
        self.exchange._channel_id = "configured"

        result = await self.exchange.get_positions()

        self.assertEqual(positions_payload["positions"], result)
        kwargs = self.exchange._api_get.call_args.kwargs
        self.assertEqual({"channel_id": "configured"}, kwargs["params"])

    async def test_get_positions_raises_when_channel_missing(self):
        with self.assertRaises(ValueError):
            await self.exchange.get_positions()

    def test_initialize_trading_pair_symbols_from_exchange_info_builds_bidict(self):
        payload = {
            "symbols": [
                {
                    "symbol": self.exchange_symbol,
                    "base_asset": self.base_asset,
                    "quote_asset": self.quote_asset,
                    "status": "TRADING",
                },
            ]
        }

        with patch.object(self.exchange, "_set_trading_pair_symbol_map") as set_map_mock:
            self.exchange._initialize_trading_pair_symbols_from_exchange_info(payload)

        mapping = set_map_mock.call_args.args[0]
        self.assertEqual(self.trading_pair, mapping[self.exchange_symbol])

    def test_quantize_order_amount_and_price_follow_trading_rule(self):
        rule = TradingRule(
            trading_pair=self.trading_pair,
            min_order_size=Decimal("0.001"),
            min_price_increment=Decimal("0.1"),
            min_base_amount_increment=Decimal("0.01"),
            min_quote_amount_increment=Decimal("0.001"),
            min_notional_size=Decimal("0"),
        )
        self.exchange._trading_rules[self.trading_pair] = rule

        price = self.exchange.quantize_order_price(self.trading_pair, Decimal("101.27"))
        amount = self.exchange.quantize_order_amount(self.trading_pair, Decimal("1.236"))

        self.assertEqual(Decimal("101.2"), price)
        self.assertEqual(Decimal("1.23"), amount)

    def test_order_not_found_checks(self):
        self.assertTrue(self.exchange._is_order_not_found_during_status_update_error(Exception("order_not_found")))
        self.assertTrue(self.exchange._is_order_not_found_during_cancelation_error(Exception("order not found")))


class YellowProExchangeAdvancedTests(IsolatedAsyncioWrapperTestCase):
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
        self.exchange = YellowProExchange(
            yellow_pro_app_session_id="session-id",
            trading_pairs=[self.trading_pair],
            trading_required=False,
        )
        self.exchange._set_trading_pair_symbol_map(bidict({self.exchange_symbol: self.trading_pair}))
        self.exchange.logger().setLevel(1)

    def _track_limit_order(self, client_order_id: str = "yellow_pro-1", amount: Decimal = Decimal("2")) -> str:
        self.exchange.start_tracking_order(
            order_id=client_order_id,
            exchange_order_id="uuid-123",
            trading_pair=self.trading_pair,
            trade_type=TradeType.BUY,
            price=Decimal("100"),
            amount=amount,
            order_type=OrderType.LIMIT,
        )
        return client_order_id

    async def test_handle_order_update_event_tracks_partial_and_full_fill(self):
        order_id = self._track_limit_order()
        order_updates = []
        trade_updates = []

        def capture_order_update(update):
            order_updates.append(update)

        def capture_trade_update(update):
            trade_updates.append(update)

        partial_event = {
            "uuid": "uuid-123",
            "market": self.exchange_symbol,
            "origin_amount": "2",
            "amount": "1",
            "price": "100",
            "state": "partial",
            "header": {"created_at": "2024-05-05T00:00:00Z"},
            "maker_id": self.exchange._app_session_id,
        }

        finished_event = dict(partial_event)
        finished_event.update({"amount": "0", "state": "done"})

        with patch.object(self.exchange._order_tracker, "process_order_update", side_effect=capture_order_update), \
                patch.object(self.exchange._order_tracker, "process_trade_update", side_effect=capture_trade_update):
            await self.exchange._handle_order_update_event(partial_event)
            # Simulate the order tracker reflecting the partial fill
            tracked_order = self.exchange._order_tracker.fetch_tracked_order(order_id)
            tracked_order.executed_amount_base = Decimal("1")
            await self.exchange._handle_order_update_event(finished_event)

        self.assertEqual(OrderState.PARTIALLY_FILLED, order_updates[0].new_state)
        self.assertEqual(OrderState.FILLED, order_updates[-1].new_state)
        self.assertEqual(Decimal("1"), trade_updates[0].fill_base_amount)
        self.assertEqual(Decimal("1"), trade_updates[-1].fill_base_amount)

    async def test_handle_order_update_event_marks_cancellation(self):
        order_id = self._track_limit_order()

        cancel_event = {
            "uuid": "uuid-123",
            "market": self.exchange_symbol,
            "amount": "2",
            "origin_amount": "2",
            "price": "100",
            "state": "cancelled",
            "header": {"created_at": "2024-05-05T01:00:00Z"},
        }

        await self.exchange._handle_order_update_event(cancel_event)
        await asyncio.sleep(0.1)

        tracked_order = (
            self.exchange._order_tracker.fetch_tracked_order(order_id)
            or self.exchange._order_tracker.fetch_cached_order(order_id)
        )
        self.assertIsNotNone(tracked_order)
        self.assertEqual(OrderState.CANCELED, tracked_order.current_state)

    async def test_place_order_includes_channel_and_leverage(self):
        post_mock = AsyncMock(return_value={"order_uuid": "uuid-abc"})
        self.exchange._api_post = post_mock

        await self.exchange._place_order(
            order_id="oid-1",
            trading_pair=self.trading_pair,
            amount=Decimal("1"),
            trade_type=TradeType.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("101.5"),
            channel_id="custom-channel",
            leverage="5",
        )

        payload = post_mock.await_args.kwargs["data"]
        self.assertEqual("custom-channel", payload["channelID"])
        self.assertEqual("5", payload["leverage"])
        self.assertEqual("limit", payload["type"])
        self.assertEqual("COINALPHAHBOT", payload["market"])

    async def test_user_stream_event_listener_routes_private_channels(self):
        order_event = {
            "uuid": "uuid-1",
            "market": self.exchange_symbol,
            "amount": "0",
            "origin_amount": "1",
            "state": "done",
            "header": {"type": "order.updated"},
        }
        balance_event = {
            "asset_symbol": self.base_asset,
            "available_balance": "5",
            "total_balance": "10",
            "header": {"type": "spot_account.balance_update"},
        }

        async def event_generator():
            yield {"push": {"channel": "private.orders", "pub": {"data": order_event}}}
            yield {"push": {"channel": "private.balance", "pub": {"data": balance_event}}}
            raise asyncio.CancelledError

        self.exchange._handle_order_update_event = AsyncMock()
        self.exchange._handle_balance_update_event = MagicMock()

        with patch.object(self.exchange, "_iter_user_event_queue", side_effect=event_generator):
            with self.assertRaises(asyncio.CancelledError):
                await self.exchange._user_stream_event_listener()

        self.exchange._handle_order_update_event.assert_awaited_once_with(order_event)
        self.exchange._handle_balance_update_event.assert_called_once_with(balance_event)

    async def test_handle_balance_update_event_updates_internal_balances(self):
        margin_event = {
            "asset_symbol": self.base_asset,
            "available_balance": "3",
            "margin_balance": "7",
        }

        self.exchange._handle_balance_update_event(margin_event)

        self.assertEqual(Decimal("3"), self.exchange._account_available_balances[self.base_asset])
        self.assertEqual(Decimal("7"), self.exchange._account_balances[self.base_asset])

        spot_event = {
            "asset_symbol": self.quote_asset,
            "available_balance": "15",
            "total_balance": "20",
        }
        self.exchange._handle_balance_update_event(spot_event)

        self.assertEqual(Decimal("15"), self.exchange._account_available_balances[self.quote_asset])
        self.assertEqual(Decimal("20"), self.exchange._account_balances[self.quote_asset])

    async def test_download_orders_snapshot_uses_cache_and_pagination(self):
        responses = [
            {"orders": [{"order_id": str(i)} for i in range(100)]},
            {"orders": [{"order_id": "target", "state": "wait"}]},
        ]

        async def side_effect(*args, **kwargs):
            page = kwargs["params"]["page"]
            return responses[page - 1]

        get_mock = AsyncMock(side_effect=side_effect)
        self.exchange._api_get = get_mock

        orders = await self.exchange._download_orders_snapshot(self.exchange_symbol, target_order_id="target")
        self.assertEqual(101, len(orders))
        self.assertEqual(2, get_mock.await_count)

    async def test_download_trades_snapshot_stops_when_no_new_entries(self):
        self.exchange._processed_trade_ids_lookup = {"1", "2"}

        responses = [
            {"trades": [{"id": "1"}, {"id": "2"}]},
            {"trades": [{"id": "3"}]},
        ]

        async def side_effect(*args, **kwargs):
            return responses.pop(0)

        get_mock = AsyncMock(side_effect=side_effect)
        self.exchange._api_get = get_mock

        trades = await self.exchange._download_trades_snapshot(stop_when_no_new=True)
        self.assertEqual([{"id": "1"}, {"id": "2"}], trades)
        self.assertEqual(1, get_mock.await_count)
