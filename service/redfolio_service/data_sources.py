from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, TypeVar

DEFAULT_AKSHARE_TIMEOUT_SECONDS = 15.0
DEFAULT_AKSHARE_REQUEST_INTERVAL_SECONDS = 0.5
T = TypeVar("T")

_no_proxy_extended = False
_no_proxy_lock = threading.Lock()


@dataclass(frozen=True)
class Quote:
    code: str
    name: str
    security_type: str
    price: float
    as_of: date
    exchange: str = ""
    industry: str = ""
    source: str = "akshare"
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class Dividend:
    ex_date: date
    cash_per_share: float
    pay_date: date | None = None
    record_date: date | None = None
    source: str = "akshare"
    payload: dict[str, Any] | None = None


class DataSourceError(RuntimeError):
    pass


def env_float(name: str, default: float, minimum: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return max(minimum, value)


def extend_no_proxy_for_cn_sources() -> None:
    global _no_proxy_extended
    with _no_proxy_lock:
        if _no_proxy_extended:
            return
        hosts = [
            ".eastmoney.com",
            "eastmoney.com",
            "82.push2.eastmoney.com",
            "push2.eastmoney.com",
            "datacenter-web.eastmoney.com",
            ".cninfo.com.cn",
            "cninfo.com.cn",
        ]
        for key in ("NO_PROXY", "no_proxy"):
            current = [item.strip() for item in os.environ.get(key, "").split(",") if item.strip()]
            merged = current + [host for host in hosts if host not in current]
            os.environ[key] = ",".join(merged)
        _no_proxy_extended = True


def normalize_code(code: str) -> str:
    return re.sub(r"[^0-9]", "", code or "")[:6]


def infer_security_type(code: str) -> str:
    clean = normalize_code(code)
    if clean.startswith(("15", "16", "18", "50", "51", "52", "56", "58")):
        return "ETF"
    return "STOCK"


def infer_exchange(code: str) -> str:
    clean = normalize_code(code)
    if clean.startswith(("5", "6", "9")):
        return "SH"
    if clean.startswith(("0", "1", "2", "3")):
        return "SZ"
    return ""


def parse_date_value(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text in {"-", "--", "---", "None", "nan", "NaT"}:
        return None
    text = text.replace("/", "-").replace(".", "-")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        match = re.search(r"(\d{4})(\d{2})(\d{2})", text)
    if not match:
        return None
    year, month, day = [int(part) for part in match.groups()]
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_float_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value != value:
            return None
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "--", "---", "None", "nan"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group(0)) if match else None


def cash_per_share_from_row(row: dict[str, Any], security_type: str | None = None) -> float | None:
    joined = " ".join(str(value) for value in row.values())
    normalized_security_type = (security_type or "").upper()
    ten_unit_pattern = r"(?:每\s*10\s*(?:股|份)?|10\s*(?:股|份)?)(?:\s|股|份|派|转|送|分红|现金){0,8}(?:派|分红|现金)\s*(\d+(?:\.\d+)?)"
    match = re.search(ten_unit_pattern, joined)
    if match:
        return float(match.group(1)) / 10

    match = re.search(r"(?:每股|每份).{0,8}(?:派|分红|现金)\s*(\d+(?:\.\d+)?)", joined)
    if match:
        return float(match.group(1))

    cash_tokens = ("每股派息", "每股分红", "每份分红", "每份派现", "派息", "派现", "现金红利")
    ratio_tokens = ("派息比例", "派现比例", "分红比例", "现金分红比例")
    ignored_tokens = ("说明", "方案", "公告", "报告")

    for key, value in row.items():
        name = str(key)
        if not any(token in name for token in cash_tokens):
            continue
        if any(token in name for token in ignored_tokens):
            continue
        number = parse_float_value(value)
        if number is None:
            continue
        has_explicit_single_unit = "每股" in name or "每份" in name
        if "10" in name or "每10" in name or any(token in name for token in ratio_tokens):
            return number / 10
        if normalized_security_type == "STOCK" and not has_explicit_single_unit:
            return number / 10
        return number

    return None


def first_date(row: dict[str, Any], candidates: tuple[str, ...]) -> date | None:
    for wanted in candidates:
        for key, value in row.items():
            if wanted in str(key):
                parsed = parse_date_value(value)
                if parsed:
                    return parsed
    return None


class AkshareDataSource:
    def __init__(self) -> None:
        extend_no_proxy_for_cn_sources()
        try:
            import akshare as ak  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on user env
            raise DataSourceError("akshare is not installed or failed to import") from exc
        self.ak = ak
        self.timeout_seconds = env_float(
            "REDFOLIO_AKSHARE_TIMEOUT_SECONDS",
            DEFAULT_AKSHARE_TIMEOUT_SECONDS,
            0.1,
        )
        self.request_interval_seconds = env_float(
            "REDFOLIO_AKSHARE_REQUEST_INTERVAL_SECONDS",
            DEFAULT_AKSHARE_REQUEST_INTERVAL_SECONDS,
            0.0,
        )
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0

    def search(self, query: str) -> list[dict[str, Any]]:
        code = normalize_code(query)
        results: list[dict[str, Any]] = []

        if code:
            results.append(
                {
                    "code": code,
                    "name": "",
                    "securityType": infer_security_type(code),
                    "exchange": infer_exchange(code),
                    "industry": "",
                }
            )

        return results

    def get_quote(self, code: str, security_type: str | None = None) -> Quote:
        clean = normalize_code(code)
        target_type = (security_type or infer_security_type(clean)).upper()
        if target_type == "ETF":
            return self._get_etf_quote(clean)
        if target_type == "ETF_LINK":
            return self._get_etf_link_quote(clean)
        return self._get_stock_quote(clean)

    def get_dividends(self, code: str, security_type: str | None = None) -> list[Dividend]:
        clean = normalize_code(code)
        target_type = (security_type or infer_security_type(clean)).upper()
        if target_type in {"ETF", "ETF_LINK"}:
            return self._get_fund_dividends(clean, target_type)
        return self._get_stock_dividends(clean)

    def _get_stock_quote(self, code: str) -> Quote:
        errors: list[str] = []
        for source_name, loader in (
            ("akshare:stock_zh_a_spot_em", self.ak.stock_zh_a_spot_em),
            ("akshare:stock_zh_a_spot_sina", self.ak.stock_zh_a_spot),
        ):
            try:
                frame = self._call_remote(source_name, loader)
                row = self._find_row(frame, code, ("代码", "code"))
                if not row:
                    errors.append(f"{source_name}: stock quote not found")
                    continue
                price = parse_float_value(row.get("最新价") or row.get("最新") or row.get("收盘"))
                if price is None:
                    errors.append(f"{source_name}: stock quote has no price")
                    continue
                return Quote(
                    code=code,
                    name=str(row.get("名称") or row.get("name") or ""),
                    security_type="STOCK",
                    price=price,
                    as_of=date.today(),
                    exchange=infer_exchange(code),
                    industry=str(row.get("行业") or ""),
                    source=source_name,
                    payload=self._jsonable(row),
                )
            except Exception as exc:
                errors.append(f"{source_name}: {exc}")
        raise DataSourceError(f"stock quote failed for {code}: " + "; ".join(errors))

    def _get_etf_quote(self, code: str) -> Quote:
        frame = self._call_remote("akshare:fund_etf_spot_em", self.ak.fund_etf_spot_em)
        row = self._find_row(frame, code, ("代码", "code"))
        if not row:
            raise DataSourceError(f"ETF quote not found for {code}")
        price = parse_float_value(row.get("最新价") or row.get("市价") or row.get("单位净值"))
        if price is None:
            raise DataSourceError(f"ETF quote has no price for {code}")
        return Quote(
            code=code,
            name=str(row.get("名称") or row.get("基金简称") or ""),
            security_type="ETF",
            price=price,
            as_of=date.today(),
            exchange=infer_exchange(code),
            industry="ETF",
            payload=self._jsonable(row),
        )

    def _get_etf_link_quote(self, code: str) -> Quote:
        errors: list[str] = []
        try:
            frame = self._call_remote("akshare:fund_open_fund_daily_em", self.ak.fund_open_fund_daily_em)
            row = self._find_row(frame, code, ("基金代码", "代码", "code"))
            if not row:
                raise DataSourceError("open fund quote not found")
            nav = self._latest_open_fund_nav(row)
            if nav is None:
                raise DataSourceError("open fund quote has no unit nav")
            as_of, price, _source_key = nav
            return Quote(
                code=code,
                name=str(row.get("基金简称") or row.get("名称") or ""),
                security_type="ETF_LINK",
                price=price,
                as_of=as_of,
                exchange="OTC",
                industry="ETF联接基金",
                source="akshare:fund_open_fund_daily_em",
                payload=self._jsonable(row),
            )
        except Exception as exc:
            errors.append(f"fund_open_fund_daily_em: {exc}")

        try:
            return self._get_etf_link_quote_from_history(code)
        except Exception as exc:
            errors.append(f"fund_open_fund_info_em: {exc}")

        raise DataSourceError(f"ETF link fund quote failed for {code}: " + "; ".join(errors))

    def _get_etf_link_quote_from_history(self, code: str) -> Quote:
        frame = self._call_remote(
            "akshare:fund_open_fund_info_em:open-fund-net-worth",
            self.ak.fund_open_fund_info_em,
            symbol=code,
            indicator="单位净值走势",
            period="1月",
        )
        candidates: list[tuple[date, float, dict[str, Any]]] = []
        for row in self._frame_records(frame):
            as_of = parse_date_value(row.get("净值日期"))
            price = parse_float_value(row.get("单位净值"))
            if as_of and price is not None:
                candidates.append((as_of, price, row))
        if not candidates:
            raise DataSourceError("open fund net worth history has no unit nav")

        as_of, price, row = max(candidates, key=lambda item: item[0])
        return Quote(
            code=code,
            name="",
            security_type="ETF_LINK",
            price=price,
            as_of=as_of,
            exchange="OTC",
            industry="ETF联接基金",
            source="akshare:fund_open_fund_info_em:open-fund-net-worth",
            payload=self._jsonable(row),
        )

    def _get_stock_dividends(self, code: str) -> list[Dividend]:
        calls = [
            ("stock_dividend_cninfo", {"symbol": code}),
            ("stock_dividend_detail", {"symbol": code, "indicator": "分红"}),
            ("stock_history_dividend_detail", {"symbol": code, "indicator": "分红"}),
        ]
        return self._call_dividend_candidates(calls, "stock-dividend", "STOCK")

    def _get_fund_dividends(self, code: str, security_type: str) -> list[Dividend]:
        calls = [
            ("fund_open_fund_info_em", {"symbol": code, "indicator": "分红送配详情"}),
        ]
        return self._call_dividend_candidates(calls, "fund-dividend", security_type)

    def _call_dividend_candidates(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        source: str,
        security_type: str,
    ) -> list[Dividend]:
        errors: list[str] = []
        successful_empty_call = False
        for function_name, kwargs in calls:
            function = getattr(self.ak, function_name, None)
            if function is None:
                continue
            try:
                frame = self._call_remote(f"akshare:{function_name}:{source}", function, **kwargs)
                dividends = self._parse_dividend_frame(frame, f"akshare:{function_name}:{source}", security_type)
                if dividends:
                    return dividends
                successful_empty_call = True
            except Exception as exc:  # pragma: no cover - remote data varies
                errors.append(f"{function_name}: {exc}")
        if errors and not successful_empty_call:
            raise DataSourceError("; ".join(errors))
        return []

    def _wait_for_rate_limit(self) -> None:
        if self.request_interval_seconds <= 0:
            return
        with self._request_lock:
            elapsed = time.monotonic() - self._last_request_at
            delay = self.request_interval_seconds - elapsed
            if delay > 0:
                time.sleep(delay)
            self._last_request_at = time.monotonic()

    def _call_remote(self, label: str, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        self._wait_for_rate_limit()
        result_queue: queue.Queue[tuple[str, T | BaseException]] = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                result_queue.put(("ok", function(*args, **kwargs)))
            except BaseException as exc:  # pragma: no cover - remote library failures vary
                result_queue.put(("error", exc))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(self.timeout_seconds)

        if thread.is_alive():
            raise DataSourceError(f"{label} timed out after {self.timeout_seconds:g}s")

        status, value = result_queue.get_nowait()
        if status == "error":
            raise value
        return value

    def _parse_dividend_frame(self, frame: Any, source: str, security_type: str) -> list[Dividend]:
        dividends: list[Dividend] = []

        for row in self._frame_records(frame):
            cash = cash_per_share_from_row(row, security_type)
            ex_date = first_date(row, ("除权除息日", "除息日", "除权日", "权益除息日", "日期"))
            if cash is None or not ex_date or cash <= 0:
                continue
            dividends.append(
                Dividend(
                    ex_date=ex_date,
                    cash_per_share=cash,
                    pay_date=first_date(row, ("红利发放日", "发放日", "派息日")),
                    record_date=first_date(row, ("权益登记日", "登记日")),
                    source=source,
                    payload=self._jsonable(row),
                )
            )

        return dividends

    @staticmethod
    def _find_row(frame: Any, code: str, keys: tuple[str, ...]) -> dict[str, Any] | None:
        for row in AkshareDataSource._frame_records(frame):
            for key in keys:
                if normalize_code(str(row.get(key, ""))) == code:
                    return row
        return None

    @staticmethod
    def _latest_open_fund_nav(row: dict[str, Any]) -> tuple[date, float, str] | None:
        candidates: list[tuple[date, float, str]] = []
        for key, value in row.items():
            key_text = str(key)
            if "单位净值" not in key_text or "累计" in key_text:
                continue
            price = parse_float_value(value)
            if price is None:
                continue
            candidates.append((parse_date_value(key_text) or date.today(), price, key_text))
        return max(candidates, key=lambda item: item[0]) if candidates else None

    @staticmethod
    def _frame_records(frame: Any) -> list[dict[str, Any]]:
        if frame is None or getattr(frame, "empty", False):
            return []
        return frame.to_dict(orient="records") if hasattr(frame, "to_dict") else []

    @staticmethod
    def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(row, ensure_ascii=False, default=str))
