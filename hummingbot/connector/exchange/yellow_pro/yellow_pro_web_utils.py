from typing import Any, Dict, Optional

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTRequest
from hummingbot.core.web_assistant.rest_pre_processors import RESTPreProcessorBase
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory


class YellowProRESTPreProcessor(RESTPreProcessorBase):

    async def pre_process(self, request: RESTRequest) -> RESTRequest:
        headers = request.headers or {}
        headers.setdefault("Content-Type", "application/json")
        request.headers = headers
        return request


def _domain_key(domain: Optional[str]) -> str:
    if domain in CONSTANTS.REST_URLS:
        return domain  # type: ignore[return-value]
    return CONSTANTS.DOMAIN


def rest_url(path_url: str, domain: str = CONSTANTS.DOMAIN) -> str:
    base_url = CONSTANTS.REST_URLS[_domain_key(domain)]
    return f"{base_url}{path_url}"


def private_rest_url(path_url: str, domain: str = CONSTANTS.DOMAIN) -> str:
    return rest_url(path_url, domain)


def public_rest_url(path_url: str, domain: str = CONSTANTS.DOMAIN) -> str:
    return rest_url(path_url, domain)


def quote_rest_url(path_url: str, domain: str = CONSTANTS.DOMAIN) -> str:
    base_url = CONSTANTS.QUOTE_URLS[_domain_key(domain)]
    return f"{base_url}{path_url}"


def wss_url(domain: str = CONSTANTS.DOMAIN) -> str:
    return CONSTANTS.WS_URLS[_domain_key(domain)]


def build_api_factory(
        throttler: Optional[AsyncThrottler] = None,
        auth: Optional[AuthBase] = None) -> WebAssistantsFactory:
    throttler = throttler or create_throttler()
    return WebAssistantsFactory(
        throttler=throttler,
        rest_pre_processors=[YellowProRESTPreProcessor()],
        auth=auth,
    )


def build_api_factory_without_auth(throttler: AsyncThrottler) -> WebAssistantsFactory:
    return WebAssistantsFactory(
        throttler=throttler,
        rest_pre_processors=[YellowProRESTPreProcessor()],
    )


def create_throttler() -> AsyncThrottler:
    return AsyncThrottler(CONSTANTS.RATE_LIMITS)


def is_exchange_information_valid(rule: Dict[str, Any]) -> bool:
    status = rule.get("status")
    if isinstance(status, str):
        status = status.upper()
    return status in {"TRADING", "ENABLED", "OPEN", "ACTIVE", None}
