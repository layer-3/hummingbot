from decimal import Decimal
from unittest import TestCase

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS
from hummingbot.connector.exchange.yellow_pro.yellow_pro_utils import DEFAULT_FEES, YellowProConfigMap


class YellowProUtilsTests(TestCase):

    def test_validate_domain_accepts_registered_values(self):
        domain = CONSTANTS.PRODUCTION_DOMAIN

        validated = YellowProConfigMap.validate_domain(domain)

        self.assertEqual(domain, validated)

    def test_validate_domain_rejects_unknown_values(self):
        with self.assertRaises(ValueError):
            YellowProConfigMap.validate_domain("invalid")

    def test_default_fees_match_expected_values(self):
        self.assertEqual(Decimal("0.001"), DEFAULT_FEES.maker_percent_fee_decimal)
        self.assertEqual(Decimal("0.001"), DEFAULT_FEES.taker_percent_fee_decimal)
