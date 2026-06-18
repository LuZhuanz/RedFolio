from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Transaction:
    id: int
    instrument_id: int
    side: str
    trade_date: date
    quantity: float
    price: float
    fees: float = 0.0


@dataclass(frozen=True)
class DividendEvent:
    id: int
    instrument_id: int
    ex_date: date
    cash_per_share: float
    pay_date: date | None = None
    status: str = "announced"
    report_year: int | None = None


def position_from_transactions(transactions: Iterable[Transaction]) -> dict[str, float]:
    shares = 0.0
    cost_basis = 0.0

    for transaction in sorted(transactions, key=lambda item: (item.trade_date, item.id)):
        side = transaction.side.upper()
        quantity = float(transaction.quantity)
        price = float(transaction.price)
        fees = float(transaction.fees or 0)

        if side == "BUY":
            shares += quantity
            cost_basis += quantity * price + fees
            continue

        if side == "SELL":
            if quantity > shares + 1e-9:
                raise ValueError("sell quantity exceeds current position")
            average_cost = cost_basis / shares if shares else 0.0
            shares -= quantity
            cost_basis -= average_cost * quantity
            if abs(shares) < 1e-9:
                shares = 0.0
                cost_basis = 0.0
            continue

        raise ValueError(f"unsupported transaction side: {transaction.side}")

    average_cost = cost_basis / shares if shares else 0.0
    return {
        "quantity": round(shares, 6),
        "cost_basis": round(cost_basis, 4),
        "average_cost": round(average_cost, 6),
    }


def shares_on_date(transactions: Iterable[Transaction], target_date: date) -> float:
    relevant = [transaction for transaction in transactions if transaction.trade_date <= target_date]
    return position_from_transactions(relevant)["quantity"]


def ttm_cash_per_share(events: Iterable[DividendEvent], as_of: date) -> float:
    events = dedupe_dividend_events(events)
    # Wide window (~400d) tolerates annual ex_date drift so a once-a-year payer
    # does not drop to zero just before its next ex_date.
    start = as_of - timedelta(days=400)
    total = sum(event.cash_per_share for event in events if start < event.ex_date <= as_of and event.cash_per_share > 0)
    return round(total, 6)


def dedupe_dividend_events(events: Iterable[DividendEvent]) -> list[DividendEvent]:
    """Collapse duplicate rows returned by multiple upstream dividend sources."""
    by_event: dict[tuple[date, float], DividendEvent] = {}

    def score(event: DividendEvent) -> tuple[bool, bool, bool, int]:
        return (
            event.report_year is not None,
            event.pay_date is not None,
            event.status == "announced",
            event.id,
        )

    for event in events:
        key = (event.ex_date, round(event.cash_per_share, 6))
        existing = by_event.get(key)
        if existing is None or score(event) > score(existing):
            by_event[key] = event

    return sorted(by_event.values(), key=lambda item: (item.ex_date, item.id))


def estimate_frequency(events: Iterable[DividendEvent], as_of: date) -> int | None:
    """Infer dividends-per-year from history by counting payments per calendar year.

    Returns the most common yearly count (mode). Requires at least two distinct
    observed years before committing to a frequency — a single payment cannot
    tell apart "annual" from "the first of four", so we return None and let the
    caller fall back to the TTM window. On a tie in the mode, returns the larger
    count to lean conservative (slightly higher base) rather than risk undercount.
    """
    eligible = [event for event in dedupe_dividend_events(events) if event.ex_date <= as_of and event.cash_per_share > 0]
    if not eligible:
        return None

    per_year = Counter(event.ex_date.year for event in eligible)
    if len(per_year) < 2:
        return None

    return max(per_year.values())


def report_year_cash_per_share(events: Iterable[DividendEvent], as_of: date) -> float | None:
    eligible = [
        event
        for event in dedupe_dividend_events(events)
        if event.ex_date <= as_of and event.cash_per_share > 0 and event.report_year is not None
    ]
    if not eligible:
        return None

    latest_report_year = max(event.report_year for event in eligible if event.report_year is not None)
    total = sum(event.cash_per_share for event in eligible if event.report_year == latest_report_year)
    return round(total, 6)


def annual_cash_per_share(events: Iterable[DividendEvent], as_of: date) -> float:
    """Annualized dividend-per-share base, frequency-aware.

    Counts the most recent ``freq`` payments (where ``freq`` is the inferred
    per-year frequency) rather than summing a fixed-day window. This is immune
    to ex_date drift: counting payments instead of days means a once-a-year
    payer is never over/under-counted at the window edge.

    Falls back to the wide TTM window when frequency cannot be inferred.
    """
    report_year_cash = report_year_cash_per_share(events, as_of)
    if report_year_cash is not None:
        return report_year_cash

    deduped_events = dedupe_dividend_events(events)
    eligible = sorted(
        (event for event in deduped_events if event.ex_date <= as_of and event.cash_per_share > 0),
        key=lambda item: item.ex_date,
        reverse=True,
    )
    if not eligible:
        return 0.0

    freq = estimate_frequency(events, as_of)
    if freq is None:
        return ttm_cash_per_share(events, as_of)

    recent = eligible[:freq]
    return round(sum(event.cash_per_share for event in recent), 6)


def reference_cash_per_share(events: Iterable[DividendEvent], as_of: date) -> float:
    eligible_events = [
        event for event in dedupe_dividend_events(events) if event.ex_date <= as_of and event.cash_per_share > 0
    ]
    return annual_cash_per_share(eligible_events, as_of)


def forecast_taxable_income(
    transactions: Iterable[Transaction],
    events: Iterable[DividendEvent],
    as_of: date,
) -> dict[str, object]:
    sorted_transactions = list(transactions)
    sorted_events = sorted(dedupe_dividend_events(events), key=lambda item: (item.ex_date, item.id))
    year_events = [event for event in sorted_events if event.ex_date.year == as_of.year]
    lines: list[dict[str, object]] = []
    known_cash_per_share = 0.0
    known_income = 0.0

    for event in year_events:
        shares = (
            shares_on_date(sorted_transactions, event.ex_date)
            if event.ex_date <= as_of
            else position_from_transactions(sorted_transactions)["quantity"]
        )
        amount = max(0.0, shares) * max(0.0, event.cash_per_share)
        known_cash_per_share += max(0.0, event.cash_per_share)
        known_income += amount
        lines.append(
            {
                "kind": "announced",
                "exDate": event.ex_date.isoformat(),
                "payDate": event.pay_date.isoformat() if event.pay_date else None,
                "cashPerShare": round(event.cash_per_share, 6),
                "quantity": round(max(0.0, shares), 6),
                "amount": round(amount, 2),
            }
        )

    baseline_cash_per_share = reference_cash_per_share(sorted_events, as_of)
    remaining_cash_per_share = max(0.0, baseline_cash_per_share - known_cash_per_share)
    current_shares = position_from_transactions(sorted_transactions)["quantity"]

    if current_shares > 0 and remaining_cash_per_share > 1e-9:
        estimated_amount = current_shares * remaining_cash_per_share
        lines.append(
            {
                "kind": "estimated",
                "exDate": None,
                "payDate": None,
                "cashPerShare": round(remaining_cash_per_share, 6),
                "quantity": round(current_shares, 6),
                "amount": round(estimated_amount, 2),
            }
        )
        known_income += estimated_amount

    status = "none"
    if any(line["kind"] == "estimated" for line in lines):
        status = "mixed" if any(line["kind"] == "announced" for line in lines) else "estimated"
    elif lines:
        status = "announced"

    return {
        "amount": round(known_income, 2),
        "status": status,
        "lines": lines,
    }


def yield_metrics(ttm_cash: float, last_price: float | None, average_cost: float | None) -> dict[str, float | None]:
    current_yield = None
    cost_yield = None

    if last_price and last_price > 0:
        current_yield = round(ttm_cash / last_price, 6)
    if average_cost and average_cost > 0:
        cost_yield = round(ttm_cash / average_cost, 6)

    return {
        "current_yield": current_yield,
        "cost_yield": cost_yield,
    }
