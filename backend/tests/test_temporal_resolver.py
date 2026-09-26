import unittest
from datetime import date

from app.chatbot.temporal_resolver import TemporalResolver


class TemporalResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = date(2026, 6, 18)

    def assert_range(self, message: str, start: str, end: str, granularity: str, source: str) -> None:
        resolved = TemporalResolver.resolve(message, reference_date=self.reference)
        self.assertIsNotNone(resolved, message)
        assert resolved is not None
        self.assertEqual(resolved.start_date, start)
        self.assertEqual(resolved.end_date, end)
        self.assertEqual(resolved.granularity, granularity)
        self.assertEqual(resolved.source, source)
        self.assertTrue(resolved.label_fr)
        self.assertTrue(resolved.label_en)

    def test_relative_days(self) -> None:
        self.assert_range("aujourd'hui", "2026-06-18", "2026-06-18", "day", "relative")
        self.assert_range("hier", "2026-06-17", "2026-06-17", "day", "relative")
        self.assert_range("avant-hier", "2026-06-16", "2026-06-16", "day", "relative")

    def test_relative_periods(self) -> None:
        self.assert_range("cette semaine", "2026-06-15", "2026-06-18", "week", "relative")
        self.assert_range("semaine dernière", "2026-06-08", "2026-06-14", "week", "relative")
        self.assert_range("ce mois", "2026-06-01", "2026-06-18", "month", "relative")
        self.assert_range("mois dernier", "2026-05-01", "2026-05-31", "month", "relative")
        self.assert_range("cette année", "2026-01-01", "2026-06-18", "year", "relative")
        self.assert_range("année dernière", "2025-01-01", "2025-12-31", "year", "relative")

    def test_explicit_dates_and_ranges(self) -> None:
        self.assert_range("le 2 février", "2026-02-02", "2026-02-02", "day", "explicit")
        self.assert_range("le 02/02", "2026-02-02", "2026-02-02", "day", "explicit")
        self.assert_range("le 2 février 2026", "2026-02-02", "2026-02-02", "day", "explicit")
        self.assert_range("entre le 2 février et le 5 février", "2026-02-02", "2026-02-05", "range", "explicit")
        self.assert_range("du 2 au 5 février", "2026-02-02", "2026-02-05", "range", "explicit")
        self.assert_range("entre 15 mars et 25 mars", "2026-03-15", "2026-03-25", "range", "explicit")
        self.assert_range("entre 1 juin et 4 juin", "2026-06-01", "2026-06-04", "range", "explicit")
        self.assert_range("entre 2 mai jusqu’à 5 mai", "2026-05-02", "2026-05-05", "range", "explicit")
        self.assert_range("du 2 mai au 5 mai", "2026-05-02", "2026-05-05", "range", "explicit")
        self.assert_range("du 2 au 5 mai", "2026-05-02", "2026-05-05", "range", "explicit")
        self.assert_range("de 9 mai à 11 mai", "2026-05-09", "2026-05-11", "range", "explicit")
        self.assert_range("entre 9 mai jusqu’à 11 mai", "2026-05-09", "2026-05-11", "range", "explicit")

    def test_last_days_months_quarters_year(self) -> None:
        self.assert_range("les 3 derniers jours", "2026-06-16", "2026-06-18", "range", "relative")
        self.assert_range("les 7 derniers jours", "2026-06-12", "2026-06-18", "range", "relative")
        self.assert_range("les 30 derniers jours", "2026-05-20", "2026-06-18", "range", "relative")
        self.assert_range("février", "2026-02-01", "2026-02-28", "month", "explicit")
        self.assert_range("premier trimestre", "2026-01-01", "2026-03-31", "quarter", "explicit")
        self.assert_range("deuxième trimestre", "2026-04-01", "2026-06-30", "quarter", "explicit")
        self.assert_range("année complète 2025", "2025-01-01", "2025-12-31", "year", "explicit")

    def test_explicit_month_comparison_keeps_both_month_ranges(self) -> None:
        left, right = TemporalResolver.explicit_comparison_dates("compare janvier et février", self.reference)

        self.assertEqual(left.start_date, "2026-01-01")
        self.assertEqual(left.end_date, "2026-01-31")
        self.assertEqual(right.start_date, "2026-02-01")
        self.assertEqual(right.end_date, "2026-02-28")


if __name__ == "__main__":
    unittest.main()
