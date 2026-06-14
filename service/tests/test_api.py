from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date

from fastapi.testclient import TestClient

from redfolio_service.calculations import reference_cash_per_share
from redfolio_service.data_sources import Dividend
from redfolio_service.main import create_app, dividend_from_row, refresh_instrument, report_year_from_raw


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), "redfolio-api-test.sqlite3")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.client = TestClient(create_app(self.db_path, "test-token"))
        self.headers = {"x-redfolio-token": "test-token"}

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_create_transaction_and_dashboard(self):
        response = self.client.post(
            "/api/transactions",
            headers=self.headers,
            json={
                "code": "600519",
                "securityType": "STOCK",
                "name": "贵州茅台",
                "side": "BUY",
                "tradeDate": "2026-01-02",
                "quantity": 100,
                "price": 1500,
                "fees": 5,
                "note": "",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        dashboard = self.client.get("/api/dashboard", headers=self.headers)

        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        body = dashboard.json()
        self.assertEqual(body["totals"]["costBasis"], 150005.0)
        self.assertEqual(body["positions"][0]["code"], "600519")

    def test_rejects_oversell(self):
        buy = self.client.post(
            "/api/transactions",
            headers=self.headers,
            json={
                "code": "510880",
                "securityType": "ETF",
                "name": "红利ETF",
                "side": "BUY",
                "tradeDate": "2026-01-02",
                "quantity": 100,
                "price": 3,
                "fees": 0,
                "note": "",
            },
        )
        self.assertEqual(buy.status_code, 200, buy.text)

        sell = self.client.post(
            "/api/transactions",
            headers=self.headers,
            json={
                "code": "510880",
                "securityType": "ETF",
                "name": "红利ETF",
                "side": "SELL",
                "tradeDate": "2026-01-03",
                "quantity": 101,
                "price": 3,
                "fees": 0,
                "note": "",
            },
        )

        self.assertEqual(sell.status_code, 422)


    def test_refresh_instrument_keeps_dividends_when_quote_fails(self):
        self.client.post(
            "/api/transactions",
            headers=self.headers,
            json={
                "code": "601398",
                "securityType": "STOCK",
                "name": "工商银行",
                "side": "BUY",
                "tradeDate": "2026-01-02",
                "quantity": 100,
                "price": 7.26,
                "fees": 0,
                "note": "",
            },
        )

        class Source:
            def get_quote(self, code, security_type):
                raise RuntimeError("quote proxy failed")

            def get_dividends(self, code, security_type):
                from datetime import date

                return [Dividend(ex_date=date(2026, 6, 1), cash_per_share=0.1, source="test")]

        from redfolio_service.main import AppState

        result = refresh_instrument(
            AppState(self.db_path, "test-token"),
            Source(),
            {"id": 1, "code": "601398", "security_type": "STOCK"},
        )

        self.assertEqual(result["status"], "partial")
        dividends = self.client.get("/api/dividends", headers=self.headers).json()["items"]
        self.assertEqual(len(dividends), 1)
        self.assertEqual(dividends[0]["cashPerShare"], 0.1)

    def test_stock_report_year_inference_avoids_ttm_overcount(self):
        rows = [
            self.dividend_row(1, "2025-07-14", 0.1646, "2025-03-29"),
            self.dividend_row(2, "2025-12-15", 0.1414, "2025-12-09"),
            self.dividend_row(3, "2026-05-13", 0.1689, "2026-05-07"),
        ]

        events = [dividend_from_row(row) for row in rows]

        self.assertEqual([event.report_year for event in events], [2024, 2025, 2025])
        self.assertEqual(reference_cash_per_share(events, date(2026, 6, 13)), 0.3103)

    def test_etf_report_year_does_not_infer_from_calendar_dates(self):
        raw_json = json.dumps({"公告日期": "2026-05-07", "除权除息日": "2026-05-13"}, ensure_ascii=False)

        self.assertIsNone(report_year_from_raw(raw_json, infer_from_dates=False))

    def test_requires_token(self):
        response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 401)

    def dividend_row(self, row_id: int, ex_date: str, cash_per_share: float, announcement_date: str) -> dict:
        return {
            "id": row_id,
            "instrument_id": 1,
            "ex_date": ex_date,
            "pay_date": None,
            "cash_per_share": cash_per_share,
            "status": "announced",
            "security_type": "STOCK",
            "raw_json": json.dumps(
                {"公告日期": announcement_date, "除权除息日": ex_date},
                ensure_ascii=False,
            ),
        }


if __name__ == "__main__":
    unittest.main()
