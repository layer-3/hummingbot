from unittest import TestCase

from hummingbot.connector.exchange.yellow_pro.yellow_pro_order_book import YellowProOrderBook
from hummingbot.core.data_type.order_book_message import OrderBookMessageType


class YellowProOrderBookTests(TestCase):

    def setUp(self) -> None:
        self.trading_pair = "COINALPHA-HBOT"

    def test_snapshot_message_from_exchange(self):
        message = {
            "trading_pair": self.trading_pair,
            "sequence_num": "1024",
            "bids": [["100.0", "1.5"]],
            "asks": [["101.0", "2.5"]],
        }

        snapshot = YellowProOrderBook.snapshot_message_from_exchange(message, timestamp=1700000000.0)

        self.assertEqual(OrderBookMessageType.SNAPSHOT, snapshot.type)
        self.assertEqual(self.trading_pair, snapshot.trading_pair)
        self.assertEqual(1024, snapshot.update_id)
        self.assertEqual(1, len(snapshot.bids))
        self.assertEqual(100.0, snapshot.bids[0].price)
        self.assertEqual(1.5, snapshot.bids[0].amount)
        self.assertEqual(1, len(snapshot.asks))
        self.assertEqual(101.0, snapshot.asks[0].price)

    def test_diff_message_from_exchange_uses_sequence_and_timestamp(self):
        message = {
            "trading_pair": self.trading_pair,
            "sequence_num": 2048,
            "created_at": "2024-05-05T00:00:00Z",
            "bids": [["100.5", "0.4"]],
            "asks": [["101.5", "0.8"]],
        }

        diff = YellowProOrderBook.diff_message_from_exchange(message)

        self.assertEqual(OrderBookMessageType.DIFF, diff.type)
        self.assertEqual(self.trading_pair, diff.trading_pair)
        self.assertAlmostEqual(1714867200.0, diff.timestamp, places=2)
        self.assertEqual(2048, diff.update_id)
        self.assertEqual(100.5, diff.bids[0].price)
        self.assertEqual(0.4, diff.bids[0].amount)

    def test_trade_message_from_exchange_interprets_side_and_timestamp(self):
        message = {
            "trading_pair": self.trading_pair,
            "trade_id": "1",
            "price": "105.1",
            "amount": "3.0",
            "side": "sell",
            "executed_at": "2024-06-01T12:00:00Z",
        }

        trade = YellowProOrderBook.trade_message_from_exchange(message)

        self.assertEqual(OrderBookMessageType.TRADE, trade.type)
        self.assertEqual(self.trading_pair, trade.trading_pair)
        self.assertEqual("1", trade.content["trade_id"])
        self.assertEqual(105.1, trade.content["price"])
        self.assertEqual(3.0, trade.content["amount"])
        self.assertEqual(2.0, trade.content["trade_type"])
