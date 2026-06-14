from __future__ import annotations

import os
import threading
import time
import unittest

from redfolio_service.data_sources import AkshareDataSource, DataSourceError


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
