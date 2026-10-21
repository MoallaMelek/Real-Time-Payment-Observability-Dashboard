import unittest
from datetime import date
from app.chatbot.temporal_resolver import TemporalResolver

class TemporalNumberWordTests(unittest.TestCase):
    def test_spelled_thirty_days_matches_numeric_window(self):
        reference = date(2026, 10, 21)
        words = TemporalResolver.resolve('les trente derniers jours', reference_date=reference)
        number = TemporalResolver.resolve('les 30 derniers jours', reference_date=reference)
        self.assertEqual(words, number)
        self.assertEqual(words.start_date, '2026-09-22')
