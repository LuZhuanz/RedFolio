import unittest
from datetime import date

from redfolio_service.calculations import (
    DividendEvent,
    Transaction,
    annual_cash_per_share,
    dedupe_dividend_events,
    estimate_frequency,
    forecast_taxable_income,
    position_from_transactions,
    reference_cash_per_share,
    shares_on_date,
    ttm_cash_per_share,
    yield_metrics,
)
from redfolio_service.data_sources import cash_per_share_from_row


class CalculationTests(unittest.TestCase):
    def test_weighted_average_cost_after_sell(self):
        transactions = [
            Transaction(1, 1, "BUY", date(2026, 1, 2), 100, 10, 1),
            Transaction(2, 1, "BUY", date(2026, 1, 3), 100, 12, 1),
            Transaction(3, 1, "SELL", date(2026, 1, 4), 50, 11, 0.5),
        ]

        position = position_from_transactions(transactions)

        self.assertEqual(position["quantity"], 150)
        self.assertAlmostEqual(position["average_cost"], 11.01, places=2)
        self.assertAlmostEqual(position["cost_basis"], 1651.5, places=2)

    def test_shares_on_ex_date(self):
        transactions = [
            Transaction(1, 1, "BUY", date(2026, 1, 1), 100, 10, 0),
            Transaction(2, 1, "SELL", date(2026, 6, 1), 40, 12, 0),
        ]

        self.assertEqual(shares_on_date(transactions, date(2026, 5, 31)), 100)
        self.assertEqual(shares_on_date(transactions, date(2026, 6, 1)), 60)

    def test_ttm_cash_per_share(self):
        events = [
            DividendEvent(1, 1, date(2025, 6, 1), 0.2),
            DividendEvent(2, 1, date(2026, 3, 1), 0.3),
            DividendEvent(3, 1, date(2026, 5, 1), 0.4),
        ]

        # Wide ~400-day window: all three payments (the oldest is exactly 365
        # days before as_of) fall inside, so they all sum in.
        self.assertEqual(ttm_cash_per_share(events, date(2026, 6, 1)), 0.9)

    def test_reference_uses_annual_cash(self):
        events = [
            DividendEvent(1, 1, date(2025, 7, 14), 0.1646, report_year=2024),
            DividendEvent(2, 1, date(2025, 12, 15), 0.1414, report_year=2025),
            DividendEvent(3, 1, date(2026, 5, 13), 0.1689, report_year=2025),
        ]

        # Frequency inference: 2025 paid 2, 2026 paid 1 → mode 2.
        # Annual base = sum of the most recent 2 payments = 0.1689 + 0.1414.
        self.assertEqual(ttm_cash_per_share(events, date(2026, 6, 13)), 0.4749)
        self.assertEqual(reference_cash_per_share(events, date(2026, 6, 13)), 0.3103)

    def test_report_year_cash_avoids_natural_year_overcount(self):
        events = [
            DividendEvent(1, 1, date(2025, 1, 7), 0.1434, report_year=2024),
            DividendEvent(2, 1, date(2025, 7, 14), 0.1646, report_year=2024),
            DividendEvent(3, 1, date(2025, 12, 15), 0.1414, report_year=2025),
            DividendEvent(4, 1, date(2026, 5, 13), 0.1689, report_year=2025),
        ]

        self.assertEqual(reference_cash_per_share(events, date(2026, 6, 17)), 0.3103)

    def test_reference_cash_dedupes_same_dividend_from_multiple_sources(self):
        events = [
            DividendEvent(1, 1, date(2025, 12, 15), 0.1414, report_year=None),
            DividendEvent(2, 1, date(2025, 12, 15), 0.1414, pay_date=date(2025, 12, 15), report_year=2025),
            DividendEvent(3, 1, date(2026, 5, 13), 0.1689, report_year=None),
            DividendEvent(4, 1, date(2026, 5, 13), 0.1689, pay_date=date(2026, 5, 13), report_year=2025),
        ]

        deduped = dedupe_dividend_events(events)

        self.assertEqual(len(deduped), 2)
        self.assertEqual(reference_cash_per_share(events, date(2026, 6, 17)), 0.3103)

    def test_forecast_uses_announced_and_no_remainder(self):
        transactions = [Transaction(1, 1, "BUY", date(2026, 1, 1), 100, 10, 0)]
        events = [
            DividendEvent(1, 1, date(2025, 9, 1), 0.4),
            DividendEvent(2, 1, date(2026, 4, 1), 0.2),
        ]

        forecast = forecast_taxable_income(transactions, events, date(2026, 6, 1))

        # Frequency: 1 payment/year (mode). Annual base = most recent 1 = 0.2,
        # which the 2026 announced payment already fully accounts for, so no
        # estimated remainder line is produced.
        self.assertEqual(forecast["status"], "announced")
        self.assertAlmostEqual(forecast["amount"], 20.0)
        self.assertEqual(len(forecast["lines"]), 1)

    def test_forecast_uses_annual_cash_reference(self):
        transactions = [Transaction(1, 1, "BUY", date(2026, 1, 1), 300, 7.26, 0)]
        events = [
            DividendEvent(1, 1, date(2025, 7, 14), 0.1646, report_year=2024),
            DividendEvent(2, 1, date(2025, 12, 15), 0.1414, report_year=2025),
            DividendEvent(3, 1, date(2026, 5, 13), 0.1689, report_year=2025),
        ]

        forecast = forecast_taxable_income(transactions, events, date(2026, 6, 13))

        # Annual base = most recent 2 payments = 0.1689 + 0.1414 = 0.3103.
        # 2026 announced = 0.1689 × 300 = 50.67; estimated remainder
        # (0.3103 − 0.1689) × 300 = 42.42; total = 93.09.
        self.assertEqual(forecast["status"], "mixed")
        self.assertAlmostEqual(forecast["amount"], 93.09)

    def test_annual_cash_last_n_payments(self):
        # Pays once per year → base = most recent single payment.
        events = [
            DividendEvent(1, 1, date(2023, 6, 10), 0.30),
            DividendEvent(2, 1, date(2024, 6, 10), 0.40),
            DividendEvent(3, 1, date(2025, 6, 10), 0.50),
        ]

        self.assertEqual(estimate_frequency(events, date(2025, 6, 11)), 1)
        self.assertEqual(annual_cash_per_share(events, date(2025, 6, 11)), 0.50)

    def test_annual_cash_avoids_overcount_window_drift(self):
        # Two annual payments ~12 months apart. A naive wide-day window at
        # as_of=2025-06-20 would catch BOTH 2024-06-10 and 2025-06-15 and
        # double-count. Frequency-aware logic (freq=1) takes only the latest.
        events = [
            DividendEvent(1, 1, date(2024, 6, 10), 0.40),
            DividendEvent(2, 1, date(2025, 6, 15), 0.50),
        ]

        self.assertEqual(annual_cash_per_share(events, date(2025, 6, 20)), 0.50)

    def test_annual_cash_avoids_undercount_window_drift(self):
        # Once-a-year payer whose ex_date drifted later (2025-05-20 → 2026-06-20).
        # At as_of=2026-05-25 the old payment has fallen out of any 365/400-day
        # window and the new one has not arrived. Counting payments (freq=1)
        # still returns the latest payment instead of dropping to zero.
        events = [
            DividendEvent(1, 1, date(2025, 5, 20), 0.50),
            DividendEvent(2, 1, date(2026, 6, 20), 0.60),
        ]

        self.assertEqual(annual_cash_per_share(events, date(2026, 5, 25)), 0.50)

    def test_annual_cash_quarterly_takes_last_four(self):
        # Pays four times a year → base = sum of the most recent 4 payments.
        events = [
            DividendEvent(1, 1, date(2025, 3, 20), 0.10),
            DividendEvent(2, 1, date(2025, 6, 20), 0.12),
            DividendEvent(3, 1, date(2025, 9, 20), 0.11),
            DividendEvent(4, 1, date(2025, 12, 20), 0.13),
            DividendEvent(5, 1, date(2026, 3, 20), 0.14),
        ]

        self.assertEqual(estimate_frequency(events, date(2026, 3, 21)), 4)
        # 4 payments in 2025, 1 so far in 2026 → mode is 4.
        # Last 4 = 0.14 + 0.13 + 0.11 + 0.12 = 0.50.
        self.assertEqual(annual_cash_per_share(events, date(2026, 3, 21)), 0.50)

    def test_annual_cash_falls_back_to_ttm_when_frequency_unknown(self):
        # Only one historical payment: cannot infer a per-year frequency.
        # Falls back to the wide TTM window.
        events = [DividendEvent(1, 1, date(2026, 3, 1), 0.30)]

        self.assertIsNone(estimate_frequency(events, date(2026, 6, 1)))
        self.assertEqual(annual_cash_per_share(events, date(2026, 6, 1)), 0.30)

    def test_cash_per_share_parses_ten_share_dividend_plan(self):
        row = {
            "派息比例": 0.16,
            "实施方案分红说明": "10派0.16元（含税）",
        }

        self.assertEqual(cash_per_share_from_row(row), 0.016)

    def test_cash_per_share_parses_fund_per_share_without_dividing_by_ten(self):
        row = {
            "年份": "2025年",
            "权益登记日": "2025-10-20",
            "除息日": "2025-10-21",
            "每份分红": "每份派现金0.0610元",
            "分红发放日": "2025-10-24",
        }

        self.assertEqual(cash_per_share_from_row(row), 0.061)

    def test_stock_cash_value_defaults_to_ten_share_unit(self):
        row = {"现金红利": 2.5}

        self.assertEqual(cash_per_share_from_row(row, security_type="STOCK"), 0.25)

    def test_etf_cash_value_defaults_to_single_unit(self):
        row = {"现金红利": 0.061}

        self.assertEqual(cash_per_share_from_row(row, security_type="ETF"), 0.061)

    def test_yield_metrics(self):
        metrics = yield_metrics(0.5, 10, 8)

        self.assertEqual(metrics["current_yield"], 0.05)
        self.assertEqual(metrics["cost_yield"], 0.0625)


if __name__ == "__main__":
    unittest.main()
