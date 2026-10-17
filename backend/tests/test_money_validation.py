import unittest
from decimal import Decimal

from app.services.money import millimes_to_tnd


class MoneyValidationTests(unittest.TestCase):
    def test_normalizes_decimal_amounts_without_losing_sign(self):
        self.assertEqual(millimes_to_tnd(Decimal('123456'), 1000), 123.456)
        self.assertEqual(millimes_to_tnd(-1000, 1000), -1)
        self.assertEqual(millimes_to_tnd(0, 1000), 0)

    def test_rejects_nonfinite_amounts_and_float_overflow(self):
        for amount in ('NaN', 'Infinity', '-Infinity', '1e10000'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                millimes_to_tnd(amount, 1000)

    def test_rejects_zero_negative_and_nonfinite_scales(self):
        for scale in (0, -1000, float('nan'), float('inf')):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                millimes_to_tnd(1000, scale)
