from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


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


def extend_no_proxy_for_cn_sources() -> None:
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
        target_type = security_type or infer_security_type(clean)
        if target_type == "ETF":
            return self._get_etf_quote(clean)
        return self._get_stock_quote(clean)

    def get_dividends(self, code: str, security_type: str | None = None) -> list[Dividend]:
        clean = normalize_code(code)
        target_type = security_type or infer_security_type(clean)
        if target_type == "ETF":
            return self._get_fund_dividends(clean)
        return self._get_stock_dividends(clean)

    def _get_stock_quote(self, code: str) -> Quote:
        errors: list[str] = []
        for source_name, loader in (
            ("akshare:stock_zh_a_spot_em", self.ak.stock_zh_a_spot_em),
            ("akshare:stock_zh_a_spot_sina", self.ak.stock_zh_a_spot),
        ):
            try:
                frame = loader()
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
        frame = self.ak.fund_etf_spot_em()
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

    def _get_stock_dividends(self, code: str) -> list[Dividend]:
        calls = [
            ("stock_dividend_cninfo", {"symbol": code}),
            ("stock_dividend_detail", {"symbol": code, "indicator": "分红"}),
            ("stock_history_dividend_detail", {"symbol": code, "indicator": "分红"}),
        ]
        return self._call_dividend_candidates(calls, "stock-dividend", "STOCK")

    def _get_fund_dividends(self, code: str) -> list[Dividend]:
        calls = [
            ("fund_open_fund_info_em", {"symbol": code, "indicator": "分红送配详情"}),
        ]
        return self._call_dividend_candidates(calls, "fund-dividend", "ETF")

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
                frame = function(**kwargs)
                dividends = self._parse_dividend_frame(frame, f"akshare:{function_name}:{source}", security_type)
                if dividends:
                    return dividends
                successful_empty_call = True
            except Exception as exc:  # pragma: no cover - remote data varies
                errors.append(f"{function_name}: {exc}")
        if errors and not successful_empty_call:
            raise DataSourceError("; ".join(errors))
        return []

    def _parse_dividend_frame(self, frame: Any, source: str, security_type: str) -> list[Dividend]:
        if frame is None or getattr(frame, "empty", False):
            return []
        rows = frame.to_dict(orient="records") if hasattr(frame, "to_dict") else []
        dividends: list[Dividend] = []

        for row in rows:
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
        if frame is None or getattr(frame, "empty", False):
            return None
        rows = frame.to_dict(orient="records") if hasattr(frame, "to_dict") else []
        for row in rows:
            for key in keys:
                if normalize_code(str(row.get(key, ""))) == code:
                    return row
        return None

    @staticmethod
    def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(row, ensure_ascii=False, default=str))

