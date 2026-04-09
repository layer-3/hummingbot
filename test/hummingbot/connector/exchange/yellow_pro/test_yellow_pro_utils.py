from decimal import Decimal
from unittest import TestCase

from hummingbot.connector.exchange.yellow_pro.yellow_pro_utils import DEFAULT_FEES


class YellowProUtilsTests(TestCase):

    def test_default_fees_match_expected_values(self):
        self.assertEqual(Decimal("0.001"), DEFAULT_FEES.maker_percent_fee_decimal)
        self.assertEqual(Decimal("0.001"), DEFAULT_FEES.taker_percent_fee_decimal)
