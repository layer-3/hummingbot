import asyncio
import csv
import logging
import os
from collections import defaultdict
from typing import Dict, Optional

from hummingbot.logger import HummingbotLogger


class PerfTracer:
    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(cls.__name__)
        return cls._logger

    def __init__(self, dump_interval: float = 60.0, csv_path: Optional[str] = None):
        self._traces: Dict[str, Dict[str, any]] = defaultdict(dict)
        self._lock = asyncio.Lock()
        self._dump_interval = dump_interval
        self._dump_task: Optional[asyncio.Task] = None
        # Use logs directory if it exists, otherwise current directory
        if csv_path:
            self._csv_path = csv_path
        else:
            logs_dir = "logs"
            if os.path.isdir(logs_dir):
                self._csv_path = os.path.join(logs_dir, "yellow_pro_perf_traces.csv")
            else:
                self._csv_path = "yellow_pro_perf_traces.csv"
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._dump_task = asyncio.create_task(self._dump_loop())
        self.logger().info("PerfTracer started.")

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._dump_task:
            self._dump_task.cancel()
            try:
                await self._dump_task
            except asyncio.CancelledError:
                pass
            self._dump_task = None
        await self._dump_to_csv()
        self.logger().info("PerfTracer stopped.")

    async def record_event(self, order_id: str, event_type: str, timestamp: float, overwrite: bool = True, **kwargs):
        async with self._lock:
            if order_id not in self._traces:
                self._traces[order_id]["order_id"] = order_id

            if overwrite or event_type not in self._traces[order_id]:
                self._traces[order_id][event_type] = timestamp

            for k, v in kwargs.items():
                if v is not None:
                    self._traces[order_id][k] = v

    async def _dump_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self._dump_interval)
                await self._dump_to_csv()
            except asyncio.CancelledError:
                break
            except Exception:
                self.logger().exception("Error in PerfTracer dump loop")

    async def _dump_to_csv(self):
        async with self._lock:
            if not self._traces:
                return

            file_exists = os.path.isfile(self._csv_path)

            # Define all possible columns
            fieldnames = [
                "order_id",
                "trace_id",
                "order_uuid",
                "request_time",
                "ack_time",
                "first_trade_time",
                "cancel_request_time",
                "cancel_ack_time"
            ]

            try:
                with open(self._csv_path, mode='a', newline='') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')

                    if not file_exists:
                        writer.writeheader()

                    for trace in self._traces.values():
                        # Format timestamps to microsecond precision
                        for key in ["request_time", "ack_time", "first_trade_time", "cancel_request_time", "cancel_ack_time"]:
                            if key in trace and isinstance(trace[key], (int, float)):
                                trace[key] = f"{trace[key]:.6f}"
                        writer.writerow(trace)

                self.logger().info(f"Dumped {len(self._traces)} traces to {self._csv_path}")
                self._traces.clear()
            except Exception:
                self.logger().exception(f"Failed to dump traces to {self._csv_path}")
