from datetime import date
import unittest

from redfolio_service.data_sources import cash_per_share_from_row
from redfolio_service.calculations import (
    DividendEvent,
    Transaction,
    forecast_taxable_income,
    position_from_transactions,
    shares_on_date,
    reference_cash_per_share,
    ttm_cash_per_share,
    yield_metrics,
)


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

        self.assertEqual(ttm_cash_per_share(events, date(2026, 6, 1)), 0.7)


    def test_reference_cash_uses_latest_report_year(self):
        events = [
            DividendEvent(1, 1, date(2025, 7, 14), 0.1646, report_year=2024),
            DividendEvent(2, 1, date(2025, 12, 15), 0.1414, report_year=2025),
            DividendEvent(3, 1, date(2026, 5, 13), 0.1689, report_year=2025),
        ]

        self.assertEqual(ttm_cash_per_share(events, date(2026, 6, 13)), 0.4749)
        self.assertEqual(reference_cash_per_share(events, date(2026, 6, 13)), 0.3103)

    def test_forecast_uses_announced_and_ttm_remainder(self):
        transactions = [Transaction(1, 1, "BUY", date(2026, 1, 1), 100, 10, 0)]
        events = [
            DividendEvent(1, 1, date(2025, 9, 1), 0.4),
            DividendEvent(2, 1, date(2026, 4, 1), 0.2),
        ]

        forecast = forecast_taxable_income(transactions, events, date(2026, 6, 1))

        self.assertEqual(forecast["status"], "mixed")
        self.assertAlmostEqual(forecast["amount"], 60.0)
        self.assertEqual(len(forecast["lines"]), 2)



    def test_forecast_uses_report_year_reference(self):
        transactions = [Transaction(1, 1, "BUY", date(2026, 1, 1), 300, 7.26, 0)]
        events = [
            DividendEvent(1, 1, date(2025, 7, 14), 0.1646, report_year=2024),
            DividendEvent(2, 1, date(2025, 12, 15), 0.1414, report_year=2025),
            DividendEvent(3, 1, date(2026, 5, 13), 0.1689, report_year=2025),
        ]

        forecast = forecast_taxable_income(transactions, events, date(2026, 6, 13))

        self.assertEqual(forecast["status"], "mixed")
        self.assertAlmostEqual(forecast["amount"], 93.09)

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

    def test_yield_metrics(self):
        metrics = yield_metrics(0.5, 10, 8)

        self.assertEqual(metrics["current_yield"], 0.05)
        self.assertEqual(metrics["cost_yield"], 0.0625)


if __name__ == "__main__":
    unittest.main()

