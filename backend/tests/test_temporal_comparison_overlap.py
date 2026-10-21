import unittest
from datetime import date
from app.chatbot.temporal_resolver import TemporalResolver

class TemporalComparisonOverlapTests(unittest.TestCase):
    def test_single_day_before_yesterday_is_not_a_comparison(self):
        for message in ('avant-hier', 'avant hier'):
            self.assertEqual(TemporalResolver.resolve_many(message, reference_date=date(2026,10,21)), [])

    def test_real_comparison_preserves_only_requested_dates(self):
        ranges = TemporalResolver.resolve_many('compare avant-hier et aujourd’hui', reference_date=date(2026,10,21))
        self.assertEqual([r.start_date for r in ranges], ['2026-10-19','2026-10-21'])
