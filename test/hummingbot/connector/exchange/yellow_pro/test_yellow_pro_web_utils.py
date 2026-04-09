from unittest import TestCase

from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS
from hummingbot.connector.exchange.yellow_pro import yellow_pro_web_utils as web_utils
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest


class YellowProWebUtilsTests(TestCase):

    def test_rest_url_uses_domain_base(self):
        path = "/test"
        domain = CONSTANTS.UAT_DOMAIN

        url = web_utils.rest_url(path, domain)

        self.assertEqual(f"{CONSTANTS.REST_URLS[domain]}{path}", url)

    def test_auth_and_quote_urls_follow_domain(self):
        path = "/auth"
        domain = CONSTANTS.PRODUCTION_DOMAIN

        auth_url = web_utils.auth_rest_url(path, domain)
        quote_url = web_utils.quote_rest_url(path, domain)

        self.assertEqual(f"{CONSTANTS.AUTH_URLS[domain]}{path}", auth_url)
        self.assertEqual(f"{CONSTANTS.QUOTE_URLS[domain]}{path}", quote_url)

    def test_build_api_factory_includes_preprocessor(self):
        factory = web_utils.build_api_factory()

        self.assertEqual(1, len(factory._rest_pre_processors))
        self.assertIsInstance(factory._rest_pre_processors[0], web_utils.YellowProRESTPreProcessor)

    def test_is_exchange_information_valid_checks_status(self):
        valid = {"status": "TRADING"}
        invalid = {"status": "DISABLED"}

        self.assertTrue(web_utils.is_exchange_information_valid(valid))
        self.assertFalse(web_utils.is_exchange_information_valid(invalid))


class YellowProRESTPreProcessorTests(IsolatedAsyncioWrapperTestCase):
    level = 0

    async def test_pre_process_adds_json_content_type(self):
        request = RESTRequest(method=RESTMethod.GET, url="https://example.io", headers={})
        pre_processor = web_utils.YellowProRESTPreProcessor()

        processed = await pre_processor.pre_process(request)

        self.assertEqual("application/json", processed.headers["Content-Type"])
        self.assertIs(request, processed)
