import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS
from hummingbot.connector.exchange.yellow_pro.yellow_pro_auth import YellowProAuth
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest, WSRequest


class YellowProAuthTests(IsolatedAsyncioWrapperTestCase):
    level = 0

    def setUp(self) -> None:
        super().setUp()
        self.api_key = "test-api-key"
        self.api_secret = "test-api-secret"
        self.auth = YellowProAuth(
            api_key=self.api_key,
            api_secret=self.api_secret,
            domain=CONSTANTS.DOMAIN,
        )

    # ------------------------------------------------------------------ helpers

    def _expected_signature(self, prehash: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ------------------------------------------------------------------ REST

    async def test_rest_authenticate_adds_required_headers(self):
        request = RESTRequest(method=RESTMethod.GET, url="https://api.example.com/spot/orders", headers={})

        fixed_ts = "1700000000"
        with patch("hummingbot.connector.exchange.yellow_pro.yellow_pro_auth.time.time", return_value=int(fixed_ts)):
            configured = await self.auth.rest_authenticate(request)

        self.assertEqual(self.api_key, configured.headers["X-API-KEY"])
        self.assertIn("X-SIGNATURE", configured.headers)
        self.assertEqual(fixed_ts, configured.headers["X-TIMESTAMP"])
        self.assertEqual("application/json", configured.headers["Content-Type"])

    async def test_rest_authenticate_signature_is_correct_for_get_no_params(self):
        url = "https://api.example.com/spot/orders"
        request = RESTRequest(method=RESTMethod.GET, url=url, headers={})

        fixed_ts = "1700000001"
        with patch("hummingbot.connector.exchange.yellow_pro.yellow_pro_auth.time.time", return_value=int(fixed_ts)):
            configured = await self.auth.rest_authenticate(request)

        prehash = f"GET/spot/orders{fixed_ts}"
        expected_sig = self._expected_signature(prehash)
        self.assertEqual(expected_sig, configured.headers["X-SIGNATURE"])

    async def test_rest_authenticate_signature_includes_sorted_params(self):
        url = "https://api.example.com/spot/orders"
        request = RESTRequest(
            method=RESTMethod.GET,
            url=url,
            params={"symbol": "BTCUSDT", "limit": "10"},
            headers={},
        )

        fixed_ts = "1700000002"
        with patch("hummingbot.connector.exchange.yellow_pro.yellow_pro_auth.time.time", return_value=int(fixed_ts)):
            configured = await self.auth.rest_authenticate(request)

        # params sorted alphabetically: limit, symbol
        canonical = "limit=10|symbol=BTCUSDT"
        prehash = f"GET/spot/orders{fixed_ts}{canonical}"
        expected_sig = self._expected_signature(prehash)
        self.assertEqual(expected_sig, configured.headers["X-SIGNATURE"])

    async def test_rest_authenticate_signature_includes_post_body(self):
        import json as _json
        url = "https://api.example.com/spot/order"
        body = {"market": "BTCUSDT", "amount": "1.0"}
        request = RESTRequest(
            method=RESTMethod.POST,
            url=url,
            data=_json.dumps(body),
            headers={},
        )

        fixed_ts = "1700000003"
        with patch("hummingbot.connector.exchange.yellow_pro.yellow_pro_auth.time.time", return_value=int(fixed_ts)):
            configured = await self.auth.rest_authenticate(request)

        # body fields sorted: amount, market
        canonical = "amount=1.0|market=BTCUSDT"
        prehash = f"POST/spot/order{fixed_ts}{canonical}"
        expected_sig = self._expected_signature(prehash)
        self.assertEqual(expected_sig, configured.headers["X-SIGNATURE"])

    async def test_rest_authenticate_preserves_existing_headers(self):
        request = RESTRequest(
            method=RESTMethod.GET,
            url="https://api.example.com/health",
            headers={"Accept": "application/json"},
        )

        configured = await self.auth.rest_authenticate(request)

        self.assertEqual("application/json", configured.headers["Accept"])
        self.assertIn("X-API-KEY", configured.headers)

    async def test_rest_authenticate_timestamp_is_recent(self):
        request = RESTRequest(method=RESTMethod.GET, url="https://api.example.com/health", headers={})

        before = int(time.time())
        configured = await self.auth.rest_authenticate(request)
        after = int(time.time())

        ts = int(configured.headers["X-TIMESTAMP"])
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)

    # ------------------------------------------------------------------ WS

    async def test_ws_authenticate_adds_required_headers(self):
        request = MagicMock(spec=WSRequest)
        request.headers = {}

        fixed_ts = "1700000010"
        with patch("hummingbot.connector.exchange.yellow_pro.yellow_pro_auth.time.time", return_value=int(fixed_ts)):
            configured = await self.auth.ws_authenticate(request)

        self.assertEqual(self.api_key, configured.headers["X-API-KEY"])
        self.assertIn("X-SIGNATURE", configured.headers)
        self.assertEqual(fixed_ts, configured.headers["X-TIMESTAMP"])

    async def test_ws_authenticate_signature_uses_ws_path(self):
        request = MagicMock(spec=WSRequest)
        request.headers = {}

        fixed_ts = "1700000011"
        with patch("hummingbot.connector.exchange.yellow_pro.yellow_pro_auth.time.time", return_value=int(fixed_ts)):
            configured = await self.auth.ws_authenticate(request)

        prehash = f"GET/ws{fixed_ts}"
        expected_sig = self._expected_signature(prehash)
        self.assertEqual(expected_sig, configured.headers["X-SIGNATURE"])

    async def test_ws_authenticate_signature_differs_from_rest_signature(self):
        rest_request = RESTRequest(method=RESTMethod.GET, url="https://api.example.com/health", headers={})
        ws_request = MagicMock(spec=WSRequest)
        ws_request.headers = {}

        fixed_ts = "1700000012"
        with patch("hummingbot.connector.exchange.yellow_pro.yellow_pro_auth.time.time", return_value=int(fixed_ts)):
            rest_configured = await self.auth.rest_authenticate(rest_request)
            ws_configured = await self.auth.ws_authenticate(ws_request)

        self.assertNotEqual(
            rest_configured.headers["X-SIGNATURE"],
            ws_configured.headers["X-SIGNATURE"],
        )

    async def test_ws_authenticate_preserves_existing_headers(self):
        request = MagicMock(spec=WSRequest)
        request.headers = {"X-Custom": "value"}

        configured = await self.auth.ws_authenticate(request)

        self.assertEqual("value", configured.headers["X-Custom"])
        self.assertIn("X-API-KEY", configured.headers)

    # ------------------------------------------------------------------ construction

    def test_auth_stores_credentials(self):
        auth = YellowProAuth(api_key="my-key", api_secret="my-secret", domain=CONSTANTS.DOMAIN)
        self.assertEqual("my-key", auth._api_key)
        self.assertEqual("my-secret", auth._api_secret)

    def test_auth_default_domain(self):
        auth = YellowProAuth(api_key="k", api_secret="s")
        self.assertEqual(CONSTANTS.DOMAIN, auth._domain)
