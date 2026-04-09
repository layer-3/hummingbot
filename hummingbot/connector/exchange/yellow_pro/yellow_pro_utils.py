from decimal import Decimal
from pydantic import ConfigDict, Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.001"),
    taker_percent_fee_decimal=Decimal("0.001"),
)

CENTRALIZED = True
EXAMPLE_PAIR = "BTC-YTEST.USD"


class YellowProConfigMap(BaseConnectorConfigMap):
    _base_config: dict = dict(BaseConnectorConfigMap.model_config)
    _base_config["extra"] = "ignore"
    model_config = ConfigDict(**_base_config)

    connector: str = "yellow_pro"

    yellow_pro_api_key: SecretStr = Field(
        default=SecretStr(""),
        json_schema_extra={
            "prompt": "Enter your YellowPro API key",
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        },
    )
    yellow_pro_api_secret: SecretStr = Field(
        default=SecretStr(""),
        json_schema_extra={
            "prompt": "Enter your YellowPro API secret",
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        },
    )
    yellow_pro_app_session_id: str = Field(
        default=...,
        json_schema_extra={
            "prompt": "Enter your YellowPro spot account session id",
            "is_secure": False,
            "is_connect_key": True,
            "prompt_on_new": True,
        },
    )


KEYS = YellowProConfigMap.model_construct()
