from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable


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
    relevant = [
        transaction
        for transaction in transactions
        if transaction.trade_date <= target_date
    ]
    return position_from_transactions(relevant)["quantity"]


def ttm_cash_per_share(events: Iterable[DividendEvent], as_of: date) -> float:
    start = as_of - timedelta(days=365)
    total = sum(
        event.cash_per_share
        for event in events
        if start < event.ex_date <= as_of and event.cash_per_share > 0
    )
    return round(total, 6)


def reference_cash_per_share(events: Iterable[DividendEvent], as_of: date) -> float:
    eligible_events = [
        event
        for event in events
        if event.ex_date <= as_of and event.cash_per_share > 0
    ]
    report_years = [event.report_year for event in eligible_events if event.report_year is not None]
    if report_years:
        latest_report_year = max(report_years)
        total = sum(
            event.cash_per_share
            for event in eligible_events
            if event.report_year == latest_report_year
        )
        return round(total, 6)

    return ttm_cash_per_share(eligible_events, as_of)


def forecast_taxable_income(
    transactions: Iterable[Transaction],
    events: Iterable[DividendEvent],
    as_of: date,
) -> dict[str, object]:
    sorted_transactions = list(transactions)
    sorted_events = sorted(events, key=lambda item: (item.ex_date, item.id))
    year_events = [event for event in sorted_events if event.ex_date.year == as_of.year]
    lines: list[dict[str, object]] = []
    known_cash_per_share = 0.0
    known_income = 0.0

    for event in year_events:
        shares = shares_on_date(sorted_transactions, event.ex_date) if event.ex_date <= as_of else position_from_transactions(sorted_transactions)["quantity"]
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

