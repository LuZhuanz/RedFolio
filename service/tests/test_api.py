from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from redfolio_service.data_sources import Dividend
from redfolio_service.main import create_app, refresh_instrument


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

    def test_requires_token(self):
        response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
