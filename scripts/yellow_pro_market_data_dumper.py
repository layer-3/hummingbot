import asyncio
import csv
import os
from datetime import datetime
from typing import Any, Dict, Set

from hummingbot import data_path
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.event.events import OrderBookTradeEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class YellowProMarketDataDumper(ScriptStrategyBase):
    """
    This script subscribes to real-time market data (order book snapshots)
    from YellowPro and writes them to CSV files.

    It uses a high-frequency loop to check for Order Book updates (via last_diff_uid)
    instead of relying on the slower on_tick (1s) interval.

    Since YellowPro does not support public trades via WebSocket, this script
    polls the trades endpoint to capture trades.
    """
    exchange = "yellow_pro"
    trading_pairs = ["BTC-YTEST.USD"]
    depth = 5

    markets = {exchange: set(trading_pairs)}

    ob_file_paths = {}
    trades_file_paths = {}
    ob_csv_writers = {}
    trades_csv_writers = {}
    current_date = None

    processed_trade_ids: Set[str] = set()
    last_processed_uids: Dict[str, int] = {}

    polling_task = None
    ob_monitor_task = None

    def __init__(self, connectors: Dict[str, ConnectorBase]):
        super().__init__(connectors)
        self.create_order_book_and_trade_files()
        self.order_book_trade_event = SourceInfoEventForwarder(self._process_public_trade)

    async def on_stop(self):
        if self.ob_monitor_task:
            self.ob_monitor_task.cancel()
        if self.polling_task:
            self.polling_task.cancel()

    def on_tick(self):
        self.ensure_background_tasks()
        self.check_and_replace_files()
        # Poll Trades (still needed as YellowPro has no WS trades)
        if self.polling_task is None or self.polling_task.done():
            self.polling_task = asyncio.ensure_future(self.fetch_and_dump_trades())

    def ensure_background_tasks(self):
        """
        Start or restart background tasks once connectors are ready.
        """
        if self.ob_monitor_task is None or self.ob_monitor_task.done():
            self.logger().info("Starting YellowPro order book monitor task.")
            self.ob_monitor_task = asyncio.ensure_future(self.monitor_order_books())

    async def monitor_order_books(self):
        while True:
            try:
                for trading_pair in self.trading_pairs:
                    ob = self.connectors[self.exchange].get_order_book(trading_pair)
                    current_uid = ob.last_diff_uid

                    # If this is the first time or the UID has changed, dump it
                    if current_uid > self.last_processed_uids.get(trading_pair, -1):
                        self.dump_order_book_snapshot(trading_pair)
                        self.last_processed_uids[trading_pair] = current_uid

                # Check frequently (e.g., every 10ms or 100ms)
                # 0.1s = 100ms. This is much faster than on_tick (1s)
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger().error(f"Error monitoring order books: {e}")
                await asyncio.sleep(1)

    def dump_order_book_snapshot(self, trading_pair: str):
        order_book = self.connectors[self.exchange].get_order_book(trading_pair)
        snapshot = order_book.snapshot
        timestamp = self.current_timestamp

        bids = snapshot[0].loc[:(self.depth - 1), ["price", "amount"]].values.tolist()
        asks = snapshot[1].loc[:(self.depth - 1), ["price", "amount"]].values.tolist()

        row = [timestamp]

        for i in range(self.depth):
            if i < len(bids):
                row.extend(bids[i])
            else:
                row.extend([0.0, 0.0])

        for i in range(self.depth):
            if i < len(asks):
                row.extend(asks[i])
            else:
                row.extend([0.0, 0.0])

        self.ob_csv_writers[trading_pair].writerow(row)
        self.ob_file_paths[trading_pair].flush()

    async def fetch_and_dump_trades(self):
        connector = self.connectors[self.exchange]
        try:
            trades = await connector._download_trades_snapshot(force_refresh=True)
            for trade in trades:
                trade_id = str(trade.get("id"))
                if trade_id in self.processed_trade_ids:
                    continue
                self.processed_trade_ids.add(trade_id)

                market_symbol = trade.get("market")
                trading_pair = None
                if market_symbol:
                    for pair in self.trading_pairs:
                        if hasattr(connector, "_trading_pair_symbol_map"):
                            if connector._trading_pair_symbol_map.inverse.get(pair) == market_symbol:
                                trading_pair = pair
                                break

                if trading_pair and trading_pair in self.trades_csv_writers:
                    self._write_trade_to_csv(trading_pair, trade)

        except Exception as e:
            self.logger().error(f"Error polling trades: {e}")

    def _write_trade_to_csv(self, trading_pair: str, trade: Dict[str, Any]):
        timestamp = trade.get("executed_at") or trade.get("timestamp") or self.current_timestamp
        price = trade.get("price")
        amount = trade.get("amount")
        side = trade.get("side", "").lower()
        row = [timestamp, price, amount, side]
        self.trades_csv_writers[trading_pair].writerow(row)
        self.trades_file_paths[trading_pair].flush()

    def _process_public_trade(self, event_tag: int, market: ConnectorBase, event: OrderBookTradeEvent):
        row = [event.timestamp, event.price, event.amount, event.type.name.lower()]
        self.trades_csv_writers[event.trading_pair].writerow(row)
        self.trades_file_paths[event.trading_pair].flush()

    def create_order_book_and_trade_files(self):
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        for trading_pair in self.trading_pairs:
            if trading_pair in self.ob_file_paths:
                self.ob_file_paths[trading_pair].close()
            if trading_pair in self.trades_file_paths:
                self.trades_file_paths[trading_pair].close()

            ob_filename = f"{self.exchange}_{trading_pair}_order_book_{self.current_date}.csv"
            ob_path = os.path.join(data_path(), ob_filename)
            ob_file = open(ob_path, "a", newline='')
            self.ob_file_paths[trading_pair] = ob_file
            self.ob_csv_writers[trading_pair] = csv.writer(ob_file)

            if os.path.getsize(ob_path) == 0:
                header = ["timestamp"]
                for i in range(1, self.depth + 1):
                    header.extend([f"bid_price_{i}", f"bid_amount_{i}"])
                for i in range(1, self.depth + 1):
                    header.extend([f"ask_price_{i}", f"ask_amount_{i}"])
                self.ob_csv_writers[trading_pair].writerow(header)

            trades_filename = f"{self.exchange}_{trading_pair}_trades_{self.current_date}.csv"
            trades_path = os.path.join(data_path(), trades_filename)
            trades_file = open(trades_path, "a", newline='')
            self.trades_file_paths[trading_pair] = trades_file
            self.trades_csv_writers[trading_pair] = csv.writer(trades_file)

            if os.path.getsize(trades_path) == 0:
                self.trades_csv_writers[trading_pair].writerow(["timestamp", "price", "amount", "side"])

    def check_and_replace_files(self):
        current_date = datetime.now().strftime("%Y-%m-%d")
        if current_date != self.current_date:
            self.create_order_book_and_trade_files()
