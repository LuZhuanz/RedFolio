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

    def test_create_etf_link_transaction_and_dashboard(self):
        response = self.client.post(
            "/api/transactions",
            headers=self.headers,
            json={
                "code": "014164",
                "securityType": "ETF_LINK",
                "name": "红利低波ETF联接A",
                "side": "BUY",
                "tradeDate": "2026-01-02",
                "quantity": 123.45,
                "price": 1.2345,
                "fees": 1.2,
                "note": "",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        dashboard = self.client.get("/api/dashboard", headers=self.headers)

        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        body = dashboard.json()
        position = body["positions"][0]
        self.assertEqual(position["code"], "014164")
        self.assertEqual(position["securityType"], "ETF_LINK")
        self.assertEqual(position["exchange"], "OTC")
        self.assertEqual(position["industry"], "ETF联接基金")
        self.assertEqual(position["quantity"], 123.45)
        self.assertEqual(body["byType"][0]["label"], "ETF_LINK")

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

    def test_etf_link_report_year_does_not_infer_from_calendar_dates(self):
        row = self.dividend_row(1, "2026-05-13", 0.061, "2026-05-07")
        row["security_type"] = "ETF_LINK"

        self.assertIsNone(dividend_from_row(row).report_year)

    def test_init_db_migrates_old_instruments_check_for_etf_link(self):
        import sqlite3

        from redfolio_service.db import init_db

        db_path = os.path.join(tempfile.gettempdir(), "redfolio-migrate-test.sqlite3")
        if os.path.exists(db_path):
            os.remove(db_path)
        connection = sqlite3.connect(db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE instruments (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  code TEXT NOT NULL UNIQUE,
                  name TEXT NOT NULL DEFAULT '',
                  security_type TEXT NOT NULL CHECK (security_type IN ('STOCK', 'ETF')),
                  exchange TEXT NOT NULL DEFAULT '',
                  industry TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO instruments (code, name, security_type, exchange, industry)
                VALUES ('510880', '红利ETF', 'ETF', 'SH', 'ETF');
                """
            )

            init_db(connection)
            connection.execute(
                """
                INSERT INTO instruments (code, name, security_type, exchange, industry)
                VALUES ('014164', '红利低波ETF联接A', 'ETF_LINK', 'OTC', 'ETF联接基金')
                """
            )
            rows = connection.execute("SELECT code, security_type FROM instruments ORDER BY code").fetchall()

            self.assertEqual(rows, [("014164", "ETF_LINK"), ("510880", "ETF")])
        finally:
            connection.close()
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_requires_token(self):
        response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 401)

    def test_database_uses_wal_mode(self):
        import sqlite3

        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200, response.text)

        with sqlite3.connect(self.db_path) as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

        self.assertEqual(mode.lower(), "wal")

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
