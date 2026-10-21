import unittest
from app.services.money import millimes_to_tnd

class MoneyExtremeTests(unittest.TestCase):
    def test_extreme_decimal_exponents_are_validation_errors(self):
        for amount, scale in [('1e1000002', 1), ('1', '1e-1000002')]:
            with self.subTest(amount=amount, scale=scale), self.assertRaises(ValueError):
                millimes_to_tnd(amount, scale)
