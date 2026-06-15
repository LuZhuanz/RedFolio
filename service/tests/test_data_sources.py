from __future__ import annotations

import os
import threading
import time
import unittest
from datetime import date

from redfolio_service.data_sources import AkshareDataSource, DataSourceError


class FakeFrame:
    def __init__(self, records):
        self.records = records
        self.empty = not records

    def to_dict(self, orient):
        self.assert_orient(orient)
        return self.records

    @staticmethod
    def assert_orient(orient):
        if orient != "records":
            raise AssertionError(f"unexpected orient: {orient}")


class AkshareRemoteCallTests(unittest.TestCase):
    def make_source(self, timeout: float = 0.5, interval: float = 0.0) -> AkshareDataSource:
        source = object.__new__(AkshareDataSource)
        source.timeout_seconds = timeout
        source.request_interval_seconds = interval
        source._request_lock = threading.Lock()
        source._last_request_at = 0.0
        return source

    def test_call_remote_times_out(self):
        source = self.make_source(timeout=0.05)
        started = time.monotonic()

        with self.assertRaisesRegex(DataSourceError, r"slow timed out after 0\.05s"):
            source._call_remote("slow", lambda: time.sleep(1))

        self.assertLess(time.monotonic() - started, 0.5)

    def test_call_remote_propagates_remote_errors(self):
        source = self.make_source()

        def fail():
            raise RuntimeError("upstream failed")

        with self.assertRaisesRegex(RuntimeError, "upstream failed"):
            source._call_remote("failing", fail)

    def test_call_remote_rate_limits_requests(self):
        source = self.make_source(interval=0.05)

        self.assertEqual(source._call_remote("first", lambda: "ok"), "ok")
        started = time.monotonic()
        self.assertEqual(source._call_remote("second", lambda: "ok"), "ok")

        self.assertGreaterEqual(time.monotonic() - started, 0.04)


class AkshareOpenFundTests(unittest.TestCase):
    def make_source(self, ak) -> AkshareDataSource:
        source = object.__new__(AkshareDataSource)
        source.ak = ak
        source.timeout_seconds = 0.5
        source.request_interval_seconds = 0.0
        source._request_lock = threading.Lock()
        source._last_request_at = 0.0
        return source

    def test_etf_link_quote_uses_open_fund_daily_nav(self):
        class Ak:
            def fund_open_fund_daily_em(self):
                return FakeFrame(
                    [
                        {
                            "基金代码": "014164",
                            "基金简称": "红利低波ETF联接A",
                            "2026-06-12-单位净值": "1.2345",
                            "2026-06-12-累计净值": "1.3345",
                            "2026-06-11-单位净值": "1.2300",
                        }
                    ]
                )

        quote = self.make_source(Ak()).get_quote("014164", "ETF_LINK")

        self.assertEqual(quote.code, "014164")
        self.assertEqual(quote.name, "红利低波ETF联接A")
        self.assertEqual(quote.security_type, "ETF_LINK")
        self.assertEqual(quote.price, 1.2345)
        self.assertEqual(quote.as_of, date(2026, 6, 12))
        self.assertEqual(quote.exchange, "OTC")
        self.assertEqual(quote.industry, "ETF联接基金")

    def test_etf_link_quote_falls_back_to_net_worth_history(self):
        class Ak:
            def fund_open_fund_daily_em(self):
                return FakeFrame([])

            def fund_open_fund_info_em(self, symbol, indicator, period):
                self.symbol = symbol
                self.indicator = indicator
                self.period = period
                return FakeFrame(
                    [
                        {"净值日期": "2026-06-10", "单位净值": "1.1000"},
                        {"净值日期": "2026-06-12", "单位净值": "1.2222"},
                    ]
                )

        ak = Ak()
        quote = self.make_source(ak).get_quote("014164", "ETF_LINK")

        self.assertEqual(ak.symbol, "014164")
        self.assertEqual(ak.indicator, "单位净值走势")
        self.assertEqual(ak.period, "1月")
        self.assertEqual(quote.price, 1.2222)
        self.assertEqual(quote.as_of, date(2026, 6, 12))
        self.assertEqual(quote.source, "akshare:fund_open_fund_info_em:open-fund-net-worth")

    def test_etf_link_dividends_use_fund_per_share_cash(self):
        class Ak:
            def fund_open_fund_info_em(self, symbol, indicator):
                self.symbol = symbol
                self.indicator = indicator
                return FakeFrame(
                    [
                        {
                            "权益登记日": "2025-10-20",
                            "除息日": "2025-10-21",
                            "每份分红": "每份派现金0.0610元",
                            "分红发放日": "2025-10-24",
                        }
                    ]
                )

        ak = Ak()
        dividends = self.make_source(ak).get_dividends("014164", "ETF_LINK")

        self.assertEqual(ak.symbol, "014164")
        self.assertEqual(ak.indicator, "分红送配详情")
        self.assertEqual(len(dividends), 1)
        self.assertEqual(dividends[0].ex_date, date(2025, 10, 21))
        self.assertEqual(dividends[0].cash_per_share, 0.061)


class NoProxyTests(unittest.TestCase):
    def test_extend_no_proxy_runs_once(self):
        from redfolio_service import data_sources

        original_flag = data_sources._no_proxy_extended
        original_upper = os.environ.get("NO_PROXY")
        original_lower = os.environ.get("no_proxy")
        try:
            data_sources._no_proxy_extended = False
            os.environ["NO_PROXY"] = "localhost"
            os.environ["no_proxy"] = "localhost"

            data_sources.extend_no_proxy_for_cn_sources()
            first = os.environ["NO_PROXY"]
            data_sources.extend_no_proxy_for_cn_sources()

            hosts = os.environ["NO_PROXY"].split(",")
            self.assertEqual(os.environ["NO_PROXY"], first)
            self.assertEqual(hosts.count("eastmoney.com"), 1)
            self.assertEqual(len(hosts), len(set(hosts)))
        finally:
            data_sources._no_proxy_extended = original_flag
            if original_upper is None:
                os.environ.pop("NO_PROXY", None)
            else:
                os.environ["NO_PROXY"] = original_upper
            if original_lower is None:
                os.environ.pop("no_proxy", None)
            else:
                os.environ["no_proxy"] = original_lower


if __name__ == "__main__":
    unittest.main()
