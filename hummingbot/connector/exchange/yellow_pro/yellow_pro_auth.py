import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qsl, urlparse

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTRequest, WSRequest


class YellowProAuth(AuthBase):

    def __init__(
            self,
            api_key: str,
            api_secret: str,
            domain: str = CONSTANTS.DOMAIN):
        self._api_key = api_key
        self._api_secret = api_secret
        self._domain = domain or CONSTANTS.DOMAIN

    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:
        return self._sign_rest_request(request)

    async def ws_authenticate(self, request: WSRequest) -> WSRequest:
        return self._sign_ws_request(request)

    def _sign_rest_request(self, request: RESTRequest) -> RESTRequest:
        headers = request.headers or {}
        timestamp = str(int(time.time()))

        parsed_url = urlparse(request.url)
        path = parsed_url.path

        params = dict(parse_qsl(parsed_url.query))
        if request.params:
            params.update(request.params)
        if request.data:
            data = json.loads(request.data) if isinstance(request.data, str) else request.data
            if isinstance(data, dict):
                params.update(data)

        canonical_field_string = ""
        if params:
            sorted_keys = sorted(params.keys())
            canonical_field_string = "|".join(f"{k}={params[k]}" for k in sorted_keys)

        prehash = f"{request.method}{path}{timestamp}{canonical_field_string}"
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers["X-API-KEY"] = self._api_key
        headers["X-SIGNATURE"] = signature
        headers["X-TIMESTAMP"] = timestamp
        headers["Content-Type"] = "application/json"
        request.headers = headers
        return request

    def _sign_ws_request(self, request: WSRequest) -> WSRequest:
        headers = request.headers or {}
        timestamp = str(int(time.time()))

        prehash = f"GET/ws{timestamp}"
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers["X-API-KEY"] = self._api_key
        headers["X-SIGNATURE"] = signature
        headers["X-TIMESTAMP"] = timestamp
        request.headers = headers
        return request
