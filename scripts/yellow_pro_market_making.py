from typing import Dict, List, Optional, Set

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class YellowProMarketMakingConfig(StrategyV2ConfigBase):
    markets: Dict[str, Set[str]]
    candles_config: List[CandlesConfig]
    controllers_config: List[str]


class YellowProMarketMaking(StrategyV2Base):
    def __init__(self, connectors: Dict[str, ConnectorBase], config: Optional[YellowProMarketMakingConfig] = None):
        if config is None:
            config = YellowProMarketMakingConfig()
        super().__init__(connectors, config)
        controllers = self.controllers.values()
        self._agg_ctrl = next((c for c in controllers if getattr(c.config, "controller_name", "") == "orderbook_aggregator"), None)
        if self._agg_ctrl is None:
            raise ValueError("Orderbook aggregator controller not found.")
        self._taker_ctrl = next((c for c in controllers if getattr(c.config, "controller_name", "") == "taker"), None)
        self._maker_ctrl = next((c for c in controllers if getattr(c.config, "controller_name", "") == "maker"), None)
        self.logger().info(f"Enabled controllers: {[getattr(c.config, 'controller_name', '') for c in controllers]}")

    def create_actions_proposal(self):
        return []

    def stop_actions_proposal(self):
        return []

    def on_tick(self):
        super().on_tick()
        snapshot = None
        if self._agg_ctrl:
            snapshot = self._agg_ctrl.get_order_book()
        if self._taker_ctrl and snapshot:
            self._taker_ctrl.update_external_snapshot(dict(snapshot))
        if self._maker_ctrl and snapshot:
            self._maker_ctrl.update_external_snapshot(dict(snapshot))
