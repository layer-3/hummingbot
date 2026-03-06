import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Set, Tuple

from hummingbot.core.event.events import OrderType
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


@dataclass
class TargetLevel:
    price: Decimal
    amount: Decimal


@dataclass
class ActiveLevel:
    price: Decimal
    amount: Decimal
    order_id: str


class CopyOrderBook(ScriptStrategyBase):

    source_exchange = "binance"
    source_trading_pair = "BTC-USDT"
    dest_exchange = "yellow_pro"
    dest_trading_pair = "BTC-YTEST.USD"
    depth = 5
    default_update_interval = 1.0  # seconds
    update_interval = default_update_interval
    default_qty_mul = Decimal("0.1")
    default_qty_min = Decimal("0.0001")
    default_qty_max = Decimal("0.1")
    price_tolerance = Decimal("0.0001")
    amount_tolerance = Decimal("0.1")
    use_best_price_crossing = True  # 是否对最佳价格订单使用反向成交而不是撤单

    markets = {
        source_exchange: {source_trading_pair},
        dest_exchange: {dest_trading_pair},
    }

    def __init__(self, connectors):
        super().__init__(connectors)
        self.update_interval = self._load_update_interval()
        self.qty_mul = self.default_qty_mul
        self.qty_min = self.default_qty_min
        self.qty_max = self.default_qty_max
        self._last_sync_timestamp = 0.0
        self._startup_cleanup_done = False
        self._startup_cancelled_order_ids: Set[str] = set()

    def on_tick(self):
        if not self._startup_cleanup_done:
            cleanup_finished = self._cancel_all_dest_orders(context="startup", tracked_ids=self._startup_cancelled_order_ids)
            if cleanup_finished:
                self._startup_cleanup_done = True
            else:
                return

        if self.current_timestamp - self._last_sync_timestamp < self.update_interval:
            return

        self._last_sync_timestamp = self.current_timestamp

        try:
            target_levels = self._fetch_source_levels()
        except Exception as exc:  # pylint: disable=broad-except
            self.logger().warning(f"Unable to fetch source snapshot: {exc}")
            return

        dest_levels = self._collect_dest_orders()
        cancel_order_ids, orders_to_create = self._diff_orders(target_levels, dest_levels)

        for side, price, amount in orders_to_create:
            if amount <= 0:
                continue
            try:
                if side == "buy":
                    self.buy(
                        connector_name=self.dest_exchange,
                        trading_pair=self.dest_trading_pair,
                        amount=amount,
                        order_type=OrderType.LIMIT,
                        price=price,
                    )
                else:
                    self.sell(
                        connector_name=self.dest_exchange,
                        trading_pair=self.dest_trading_pair,
                        amount=amount,
                        order_type=OrderType.LIMIT,
                        price=price,
                    )
                self.logger().info(f"Placed dest {side} {amount} {self.dest_trading_pair} @ {price}.")
            except Exception as exc:  # pylint: disable=broad-except
                self.logger().error(f"Failed to place dest {side} order @ {price}: {exc}")

        time.sleep(0.1)
        for order_id in cancel_order_ids:
            try:
                # 检查是否是最佳价格订单，如果是且启用了交叉成交功能，则使用反向订单
                if self.use_best_price_crossing:
                    order_result = self._find_best_price_order_to_cross(order_id, dest_levels)
                    if order_result is not None:
                        order, side = order_result
                        self._cross_best_price_order(order, side)
                        self.logger().info(f"Crossed best price dest order {order_id} instead of cancelling.")
                        continue

                self.cancel(self.dest_exchange, self.dest_trading_pair, order_id)
                self.logger().info(f"Cancelled dest order {order_id} to align with source.")
            except Exception as exc:  # pylint: disable=broad-except
                self.logger().error(f"Failed to cancel dest order {order_id}: {exc}")

    def _fetch_source_levels(self) -> Dict[str, List[TargetLevel]]:
        connector = self.connectors[self.source_exchange]
        order_book = connector.get_order_book(self.source_trading_pair)
        if order_book is None:
            raise RuntimeError("Source order book not ready.")

        bids_df, asks_df = order_book.snapshot
        bids = self._levels_from_snapshot(bids_df, is_bid=True)
        asks = self._levels_from_snapshot(asks_df, is_bid=False)

        if len(bids) < self.depth or len(asks) < self.depth:
            self.logger().warning(
                f"Source snapshot has insufficient depth (bids: {len(bids)}, asks: {len(asks)})."
            )

        return {"buy": bids[: self.depth], "sell": asks[: self.depth]}

    def _collect_dest_orders(self) -> Dict[str, List[ActiveLevel]]:
        levels: Dict[str, List[ActiveLevel]] = {"buy": [], "sell": []}
        for order in self.get_active_orders(self.dest_exchange):
            if order.trading_pair != self.dest_trading_pair:
                continue

            price = self._as_decimal(order.price)
            quantity = self._as_decimal(order.quantity)
            filled = self._as_decimal(order.filled_quantity)
            remaining = quantity - filled

            if remaining <= 0:
                continue

            levels["buy" if order.is_buy else "sell"].append(
                ActiveLevel(price=price, amount=remaining, order_id=order.client_order_id)
            )

        levels["buy"].sort(key=lambda entry: entry.price, reverse=True)
        levels["sell"].sort(key=lambda entry: entry.price)
        return levels

    def _diff_orders(
        self,
        target_levels: Dict[str, List[TargetLevel]],
        dest_levels: Dict[str, List[ActiveLevel]],
    ) -> Tuple[List[str], List[Tuple[str, Decimal, Decimal]]]:
        cancel_candidates: Dict[str, List[ActiveLevel]] = {"buy": [], "sell": []}
        cancel_ids: List[str] = []
        to_create: List[Tuple[str, Decimal, Decimal]] = []

        unmatched_targets = {
            side: list(levels)
            for side, levels in target_levels.items()
        }

        for side in ("buy", "sell"):
            for order in dest_levels.get(side, []):
                match_idx = self._find_match(order, unmatched_targets[side])
                if match_idx is None:
                    cancel_candidates[side].append(order)
                else:
                    unmatched_targets[side].pop(match_idx)

        for side in ("buy", "sell"):
            for level in unmatched_targets[side]:
                to_create.append((side, level.price, level.amount))

        cancel_ids.extend(self._order_cancellations_by_outer_layer(cancel_candidates))

        return cancel_ids, to_create

    def _order_cancellations_by_outer_layer(self, cancel_candidates: Dict[str, List[ActiveLevel]]) -> List[str]:
        """撤单时先撤最外层报价: 买单从最低价开始, 卖单从最高价开始, 逐层向内交替撤单"""
        cancel_order_ids: List[str] = []

        buy_orders = sorted(cancel_candidates.get("buy", []), key=lambda order: order.price)
        sell_orders = sorted(cancel_candidates.get("sell", []), key=lambda order: order.price, reverse=True)

        buy_idx = 0
        sell_idx = 0
        while buy_idx < len(buy_orders) or sell_idx < len(sell_orders):
            if buy_idx < len(buy_orders):
                cancel_order_ids.append(buy_orders[buy_idx].order_id)
                buy_idx += 1
            if sell_idx < len(sell_orders):
                cancel_order_ids.append(sell_orders[sell_idx].order_id)
                sell_idx += 1

        return cancel_order_ids

    def _levels_from_snapshot(self, snapshot_df, is_bid: bool) -> List[TargetLevel]:
        levels: List[TargetLevel] = []
        ordered_df = snapshot_df.head(self.depth)
        if is_bid:
            ordered_df = ordered_df.sort_values(by="price", ascending=False)
        else:
            ordered_df = ordered_df.sort_values(by="price", ascending=True)

        for _, row in ordered_df.iterrows():
            price = self._as_decimal(row["price"])
            amount = self._as_decimal(row["amount"])
            scaled_amount = amount * self.qty_mul
            if scaled_amount <= 0:
                continue
            # 应用最小和最大数量限制
            adjusted_amount = max(scaled_amount, self.qty_min)
            adjusted_amount = min(adjusted_amount, self.qty_max)
            levels.append(TargetLevel(price=price, amount=adjusted_amount))
        return levels

    def _find_match(self, order: ActiveLevel, targets: List[TargetLevel]) -> int | None:
        for idx, target in enumerate(targets):
            if self._is_close(order.price, target.price, self.price_tolerance) and self._is_close(
                order.amount, target.amount, self.amount_tolerance
            ):
                return idx
        return None

    @staticmethod
    def _as_decimal(value) -> Decimal:
        if isinstance(value, Decimal):
            if value.is_nan():
                return Decimal("0")
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            return Decimal("0")

    @staticmethod
    def _is_close(first: Decimal, second: Decimal, tolerance: Decimal) -> bool:
        return abs(first - second) <= tolerance

    def _load_update_interval(self) -> float:
        raw_value = os.getenv("COPY_ORDERBOOK_INTERVAL")
        if raw_value is None:
            return self.default_update_interval
        try:
            parsed = float(raw_value)
            if parsed <= 0:
                raise ValueError("Interval must be positive")
            return parsed
        except ValueError:
            self.logger().warning(
                f"Invalid COPY_ORDERBOOK_INTERVAL value '{raw_value}', defaulting to {self.default_update_interval}s."
            )
            return self.default_update_interval

    def _find_best_price_order_to_cross(self, order_id: str, dest_levels: Dict[str, List[ActiveLevel]]) -> Tuple[ActiveLevel, str] | None:
        """查找要撤销的订单是否是最佳价格订单，返回(订单, 方向)或None"""
        for side in ("buy", "sell"):
            for order in dest_levels.get(side, []):
                if order.order_id == order_id:
                    # 检查是否是该方向的最高买价或最低卖价
                    if side == "buy" and len(dest_levels["buy"]) > 0 and order == dest_levels["buy"][0]:  # 最高买价
                        return order, "buy"
                    elif side == "sell" and len(dest_levels["sell"]) > 0 and order == dest_levels["sell"][0]:  # 最低卖价
                        return order, "sell"
        return None

    def _cross_best_price_order(self, order: ActiveLevel, side: str):
        """用反向订单将最佳价格订单成交"""
        if side == "buy":
            # 这是一个买单，我们需要下一个更低价的卖单来成交它
            # 卖单价格要比买单价格略低以确保成交
            cross_price = order.price * Decimal("0.9999")  # 0.01%的价格滑点
            self.sell(
                connector_name=self.dest_exchange,
                trading_pair=self.dest_trading_pair,
                amount=order.amount,
                order_type=OrderType.LIMIT,
                price=cross_price,
            )
            self.logger().info(f"Placed crossing sell order {order.amount} @ {cross_price} to hit buy order {order.order_id}")
        else:
            # 这是一个卖单，我们需要下一个更高价的买单来成交它
            # 买单价格要比卖单价格略高以确保成交
            cross_price = order.price * Decimal("1.0001")  # 0.01%的价格滑点
            self.buy(
                connector_name=self.dest_exchange,
                trading_pair=self.dest_trading_pair,
                amount=order.amount,
                order_type=OrderType.LIMIT,
                price=cross_price,
            )
            self.logger().info(f"Placed crossing buy order {order.amount} @ {cross_price} to hit sell order {order.order_id}")

    async def on_stop(self):
        self.logger().info("Stopping CopyOrderBook script, cancelling all destination orders.")
        self._cancel_all_dest_orders(context="shutdown")
        await super().on_stop()

    @classmethod
    def logger(cls):  # type: ignore[override]
        return logging.getLogger(__name__)

    def _cancel_all_dest_orders(self, context: str, tracked_ids: Set[str] | None = None) -> bool:
        """
        Cancel every outstanding order on the destination exchange for the configured trading pair.
        Returns True when there are no outstanding orders remaining so the caller can proceed.
        """
        active_orders = [
            order for order in self.get_active_orders(self.dest_exchange)
            if order.trading_pair == self.dest_trading_pair
        ]

        if not active_orders:
            if tracked_ids is not None:
                tracked_ids.clear()
            return True

        for order in active_orders:
            if tracked_ids is not None and order.client_order_id in tracked_ids:
                continue

            try:
                self.cancel(self.dest_exchange, self.dest_trading_pair, order.client_order_id)
                self.logger().info(
                    f"Cancelled dest order {order.client_order_id} during {context} cleanup to ensure a clean slate."
                )
                if tracked_ids is not None:
                    tracked_ids.add(order.client_order_id)
            except Exception as exc:  # pylint: disable=broad-except
                if tracked_ids is not None:
                    tracked_ids.discard(order.client_order_id)
                self.logger().error(f"Failed to cancel dest order {order.client_order_id} during {context} cleanup: {exc}")

        return False
