from __future__ import annotations

import argparse
import json
import re
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .calculations import (
    DividendEvent,
    Transaction,
    forecast_taxable_income,
    position_from_transactions,
    reference_cash_per_share,
    yield_metrics,
)
from .data_sources import (
    AkshareDataSource,
    DataSourceError,
    infer_exchange,
    infer_security_type,
    normalize_code,
)
from .db import connect, ensure_db_parent, init_db, rows_to_dicts

SUPPORTED_SECURITY_TYPES = {"STOCK", "ETF", "ETF_LINK"}


class TransactionIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    securityType: str | None = None
    name: str | None = None
    side: str
    tradeDate: date
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)
    fees: float = Field(default=0, ge=0)
    note: str = ""


class AppState:
    def __init__(self, db_path: str, token: str) -> None:
        ensure_db_parent(db_path)
        self.db_path = db_path
        self.token = token

    @contextmanager
    def connection(self):
        connection = connect(self.db_path)
        init_db(connection)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def create_app(db_path: str, token: str) -> FastAPI:
    state = AppState(db_path, token)
    app = FastAPI(title="RedFolio API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_token(x_redfolio_token: Annotated[str | None, Header()] = None) -> None:
        if state.token and x_redfolio_token != state.token:
            raise HTTPException(status_code=401, detail="invalid redfolio token")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        with state.connection():
            return {"status": "ok"}

    @app.get("/api/instruments/search", dependencies=[Depends(require_token)])
    def search_instruments(query: str) -> dict[str, list[dict[str, Any]]]:
        clean_query = query.strip()
        with state.connection() as connection:
            local_rows = connection.execute(
                """
                SELECT * FROM instruments
                WHERE code LIKE ? OR name LIKE ?
                ORDER BY code
                LIMIT 12
                """,
                (f"%{clean_query}%", f"%{clean_query}%"),
            ).fetchall()
            results = [instrument_response(dict(row)) for row in local_rows]

        if results:
            return {"items": results}

        code = normalize_code(clean_query)
        if not code:
            return {"items": []}

        return {
            "items": [
                {
                    "id": None,
                    "code": code,
                    "name": "",
                    "securityType": infer_security_type(code),
                    "exchange": infer_exchange(code),
                    "industry": "",
                }
            ]
        }

    @app.get("/api/transactions", dependencies=[Depends(require_token)])
    def list_transactions() -> dict[str, list[dict[str, Any]]]:
        with state.connection() as connection:
            rows = connection.execute(
                """
                SELECT t.*, i.code, i.name, i.security_type
                FROM transactions t
                JOIN instruments i ON i.id = t.instrument_id
                ORDER BY t.trade_date DESC, t.id DESC
                """
            ).fetchall()
        return {"items": [transaction_response(dict(row)) for row in rows]}

    @app.post("/api/transactions", dependencies=[Depends(require_token)])
    def create_transaction(payload: TransactionIn) -> dict[str, Any]:
        with state.connection() as connection:
            instrument_id = upsert_instrument(
                connection,
                payload.code,
                payload.securityType,
                payload.name or "",
            )
            cursor = connection.execute(
                """
                INSERT INTO transactions (instrument_id, side, trade_date, quantity, price, fees, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instrument_id,
                    normalize_side(payload.side),
                    payload.tradeDate.isoformat(),
                    payload.quantity,
                    payload.price,
                    payload.fees,
                    payload.note.strip(),
                ),
            )
            validate_position(connection, instrument_id)
            connection.commit()
            row = connection.execute(
                """
                SELECT t.*, i.code, i.name, i.security_type
                FROM transactions t
                JOIN instruments i ON i.id = t.instrument_id
                WHERE t.id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
        return transaction_response(dict(row))

    @app.put("/api/transactions/{transaction_id}", dependencies=[Depends(require_token)])
    def update_transaction(transaction_id: int, payload: TransactionIn) -> dict[str, Any]:
        with state.connection() as connection:
            existing = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="transaction not found")
            instrument_id = upsert_instrument(
                connection,
                payload.code,
                payload.securityType,
                payload.name or "",
            )
            connection.execute(
                """
                UPDATE transactions
                SET instrument_id = ?, side = ?, trade_date = ?, quantity = ?, price = ?, fees = ?, note = ?
                WHERE id = ?
                """,
                (
                    instrument_id,
                    normalize_side(payload.side),
                    payload.tradeDate.isoformat(),
                    payload.quantity,
                    payload.price,
                    payload.fees,
                    payload.note.strip(),
                    transaction_id,
                ),
            )
            validate_position(connection, int(existing["instrument_id"]))
            validate_position(connection, instrument_id)
            connection.commit()
            row = connection.execute(
                """
                SELECT t.*, i.code, i.name, i.security_type
                FROM transactions t
                JOIN instruments i ON i.id = t.instrument_id
                WHERE t.id = ?
                """,
                (transaction_id,),
            ).fetchone()
        return transaction_response(dict(row))

    @app.delete("/api/transactions/{transaction_id}", dependencies=[Depends(require_token)])
    def delete_transaction(transaction_id: int) -> dict[str, bool]:
        with state.connection() as connection:
            existing = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="transaction not found")
            connection.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
            validate_position(connection, int(existing["instrument_id"]))
            connection.commit()
        return {"ok": True}

    @app.get("/api/positions", dependencies=[Depends(require_token)])
    def get_positions(as_of: date | None = None) -> dict[str, list[dict[str, Any]]]:
        return {"items": build_positions(state, as_of or date.today())}

    @app.get("/api/dashboard", dependencies=[Depends(require_token)])
    def get_dashboard(as_of: date | None = None) -> dict[str, Any]:
        positions = build_positions(state, as_of or date.today())
        totals = {
            "marketValue": round(sum(item["marketValue"] for item in positions), 2),
            "costBasis": round(sum(item["costBasis"] for item in positions), 2),
            "forecastIncome": round(sum(item["forecastIncome"] for item in positions), 2),
            "unrealizedPnl": round(sum(item["unrealizedPnl"] for item in positions), 2),
        }
        totals["currentYield"] = (
            round(totals["forecastIncome"] / totals["marketValue"], 6) if totals["marketValue"] else None
        )
        totals["costYield"] = round(totals["forecastIncome"] / totals["costBasis"], 6) if totals["costBasis"] else None
        return {
            "totals": totals,
            "positions": positions,
            "byType": group_amounts(positions, "securityType", "marketValue"),
            "byIndustry": group_amounts(positions, "industry", "marketValue"),
            "dividendContribution": group_amounts(positions, "name", "forecastIncome", fallback_key="code"),
        }

    @app.get("/api/dividends", dependencies=[Depends(require_token)])
    def list_dividends(instrument_id: int | None = None) -> dict[str, list[dict[str, Any]]]:
        query = """
            SELECT d.*, i.code, i.name, i.security_type
            FROM dividend_events d
            JOIN instruments i ON i.id = d.instrument_id
        """
        params: tuple[Any, ...] = ()
        if instrument_id:
            query += " WHERE d.instrument_id = ?"
            params = (instrument_id,)
        query += " ORDER BY d.ex_date DESC, d.id DESC"
        with state.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return {"items": [dividend_response(dict(row)) for row in rows]}

    @app.post("/api/refresh", dependencies=[Depends(require_token)])
    def refresh_data() -> dict[str, Any]:
        started = datetime.now(UTC).isoformat(timespec="seconds")
        with state.connection() as connection:
            job_id = connection.execute("INSERT INTO refresh_jobs (started_at) VALUES (?)", (started,)).lastrowid
            instruments = rows_to_dicts(
                connection.execute(
                    """
                    SELECT DISTINCT i.*
                    FROM instruments i
                    JOIN transactions t ON t.instrument_id = i.id
                    ORDER BY i.code
                    """
                ).fetchall()
            )
            connection.commit()

        items: list[dict[str, Any]] = []
        try:
            source = AkshareDataSource()
        except DataSourceError as exc:
            mark_job(state, job_id, "failed", str(exc))
            raise HTTPException(status_code=503, detail=str(exc))

        for instrument in instruments:
            result = refresh_instrument(state, source, instrument)
            items.append(result)

        ok_items = [item for item in items if item["status"] == "ok"]
        partial_items = [item for item in items if item["status"] == "partial"]
        failed_items = [item for item in items if item["status"] == "failed"]
        job_status = (
            "ok" if not partial_items and not failed_items else "partial" if ok_items or partial_items else "failed"
        )
        mark_job(
            state, job_id, job_status, f"{len(ok_items)} ok, {len(partial_items)} partial, {len(failed_items)} failed"
        )
        return {
            "items": items,
            "refreshed": len(ok_items),
            "partial": len(partial_items),
            "failed": len(failed_items),
        }

    return app


def normalize_side(side: str) -> str:
    upper = side.upper()
    if upper not in {"BUY", "SELL"}:
        raise HTTPException(status_code=422, detail="side must be BUY or SELL")
    return upper


def default_exchange_for_type(code: str, security_type: str) -> str:
    return "OTC" if security_type == "ETF_LINK" else infer_exchange(code)


def default_industry_for_type(security_type: str) -> str:
    if security_type == "ETF_LINK":
        return "ETF联接基金"
    if security_type == "ETF":
        return "ETF"
    return ""


def display_industry(security_type: str, industry: str | None) -> str:
    return industry or default_industry_for_type(security_type) or "未分类"


def upsert_instrument(connection, code: str, security_type: str | None, name: str = "") -> int:
    clean = normalize_code(code)
    if len(clean) != 6:
        raise HTTPException(status_code=422, detail="security code must contain 6 digits")
    target_type = (security_type or infer_security_type(clean)).upper()
    if target_type not in SUPPORTED_SECURITY_TYPES:
        raise HTTPException(status_code=422, detail="securityType must be STOCK, ETF, or ETF_LINK")
    exchange = default_exchange_for_type(clean, target_type)
    default_industry = default_industry_for_type(target_type)

    existing = connection.execute("SELECT * FROM instruments WHERE code = ?", (clean,)).fetchone()
    if existing:
        connection.execute(
            """
            UPDATE instruments
            SET name = COALESCE(NULLIF(?, ''), name),
                security_type = ?,
                exchange = ?,
                industry = CASE
                    WHEN ? = '' AND security_type != ? THEN ''
                    WHEN ? != '' AND (industry = '' OR security_type != ?) THEN ?
                    ELSE industry
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE code = ?
            """,
            (
                name.strip(),
                target_type,
                exchange,
                default_industry,
                target_type,
                default_industry,
                target_type,
                default_industry,
                clean,
            ),
        )
        return int(existing["id"])

    cursor = connection.execute(
        """
        INSERT INTO instruments (code, name, security_type, exchange, industry)
        VALUES (?, ?, ?, ?, ?)
        """,
        (clean, name.strip(), target_type, exchange, default_industry),
    )
    return int(cursor.lastrowid)


def validate_position(connection, instrument_id: int) -> None:
    rows = connection.execute(
        "SELECT * FROM transactions WHERE instrument_id = ? ORDER BY trade_date, id",
        (instrument_id,),
    ).fetchall()
    transactions = [transaction_from_row(dict(row)) for row in rows]
    try:
        position_from_transactions(transactions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def transaction_from_row(row: dict[str, Any]) -> Transaction:
    return Transaction(
        id=int(row["id"]),
        instrument_id=int(row["instrument_id"]),
        side=row["side"],
        trade_date=date.fromisoformat(row["trade_date"]),
        quantity=float(row["quantity"]),
        price=float(row["price"]),
        fees=float(row["fees"] or 0),
    )


def dividend_from_row(row: dict[str, Any]) -> DividendEvent:
    return DividendEvent(
        id=int(row["id"]),
        instrument_id=int(row["instrument_id"]),
        ex_date=date.fromisoformat(row["ex_date"]),
        pay_date=date.fromisoformat(row["pay_date"]) if row.get("pay_date") else None,
        cash_per_share=float(row["cash_per_share"]),
        status=row.get("status") or "announced",
        report_year=report_year_from_row(row),
    )


def report_year_from_row(row: dict[str, Any]) -> int | None:
    existing = row.get("report_year")
    if existing:
        try:
            return int(existing)
        except (TypeError, ValueError):
            pass

    if (row.get("security_type") or row.get("securityType") or "").upper() != "STOCK":
        return None

    raw_json = row.get("raw_json")
    if not raw_json:
        return None

    try:
        payload = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    for key in ("报告时间", "报告期", "报告年度"):
        value = payload.get(key)
        if value is None:
            continue
        parsed = parse_report_year(value)
        if parsed is not None:
            return parsed
    return None


def parse_report_year(value: Any) -> int | None:
    match = None
    if value is not None:
        match = re.search(r"(?:19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def build_positions(state: AppState, as_of: date) -> list[dict[str, Any]]:
    with state.connection() as connection:
        instruments = rows_to_dicts(
            connection.execute(
                """
                SELECT DISTINCT i.*
                FROM instruments i
                JOIN transactions t ON t.instrument_id = i.id
                ORDER BY i.code
                """
            ).fetchall()
        )
        transaction_rows = rows_to_dicts(
            connection.execute("SELECT * FROM transactions ORDER BY trade_date, id").fetchall()
        )
        dividend_rows = rows_to_dicts(
            connection.execute(
                """
                SELECT d.*, i.security_type
                FROM dividend_events d
                JOIN instruments i ON i.id = d.instrument_id
                ORDER BY d.ex_date, d.id
                """
            ).fetchall()
        )
        snapshot_rows = rows_to_dicts(
            connection.execute(
                """
                SELECT *
                FROM (
                  SELECT
                    ms.*,
                    ROW_NUMBER() OVER (
                      PARTITION BY ms.instrument_id
                      ORDER BY ms.as_of DESC, ms.id DESC
                    ) AS row_number
                  FROM market_snapshots ms
                )
                WHERE row_number = 1
                """
            ).fetchall()
        )

    transactions_by_instrument: dict[int, list[Transaction]] = {}
    for row in transaction_rows:
        transactions_by_instrument.setdefault(int(row["instrument_id"]), []).append(transaction_from_row(row))

    dividends_by_instrument: dict[int, list[DividendEvent]] = {}
    for row in dividend_rows:
        dividends_by_instrument.setdefault(int(row["instrument_id"]), []).append(dividend_from_row(row))

    latest_snapshot = {int(row["instrument_id"]): row for row in snapshot_rows}
    positions: list[dict[str, Any]] = []

    for instrument in instruments:
        instrument_id = int(instrument["id"])
        transactions = transactions_by_instrument.get(instrument_id, [])
        dividends = dividends_by_instrument.get(instrument_id, [])
        position = position_from_transactions(transactions)
        if position["quantity"] <= 0:
            continue

        snapshot = latest_snapshot.get(instrument_id)
        last_price = float(snapshot["price"]) if snapshot else None
        market_value = (last_price or position["average_cost"]) * position["quantity"]
        reference_cash = reference_cash_per_share(dividends, as_of)
        forecast = forecast_taxable_income(transactions, dividends, as_of)
        yields = yield_metrics(reference_cash, last_price, position["average_cost"])
        display_name = instrument["name"] or instrument["code"]

        positions.append(
            {
                "instrumentId": instrument_id,
                "code": instrument["code"],
                "name": display_name,
                "securityType": instrument["security_type"],
                "exchange": instrument["exchange"],
                "industry": display_industry(instrument["security_type"], instrument["industry"]),
                "quantity": position["quantity"],
                "averageCost": position["average_cost"],
                "costBasis": position["cost_basis"],
                "lastPrice": round(last_price, 4) if last_price is not None else None,
                "marketValue": round(market_value, 2),
                "unrealizedPnl": round(market_value - position["cost_basis"], 2),
                "ttmCashPerShare": reference_cash,
                "currentYield": yields["current_yield"],
                "costYield": yields["cost_yield"],
                "forecastIncome": forecast["amount"],
                "forecastStatus": forecast["status"],
                "forecastLines": forecast["lines"],
                "dataAsOf": snapshot["as_of"] if snapshot else None,
            }
        )

    return sorted(positions, key=lambda item: item["marketValue"], reverse=True)


def refresh_instrument(state: AppState, source: AkshareDataSource, instrument: dict[str, Any]) -> dict[str, Any]:
    code = instrument["code"]
    quote = None
    dividends = []
    quote_error = None
    dividend_error = None
    dividends_loaded = False

    try:
        quote = source.get_quote(code, instrument["security_type"])
    except Exception as exc:
        quote_error = str(exc)

    try:
        dividends = source.get_dividends(code, instrument["security_type"])
        dividends_loaded = True
    except Exception as exc:
        dividend_error = str(exc)

    if quote is not None or dividends_loaded:
        with state.connection() as connection:
            if quote is not None:
                connection.execute(
                    """
                    UPDATE instruments
                    SET name = COALESCE(NULLIF(?, ''), name),
                        security_type = ?,
                        exchange = COALESCE(NULLIF(?, ''), exchange),
                        industry = COALESCE(NULLIF(?, ''), industry),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (quote.name, quote.security_type, quote.exchange, quote.industry, instrument["id"]),
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO market_snapshots (instrument_id, price, as_of, source, payload_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        instrument["id"],
                        quote.price,
                        quote.as_of.isoformat(),
                        quote.source,
                        json.dumps(quote.payload or {}, ensure_ascii=False),
                    ),
                )
            if dividends_loaded:
                for dividend in dividends:
                    connection.execute(
                        """
                        DELETE FROM dividend_events
                        WHERE instrument_id = ?
                          AND ex_date = ?
                          AND (source = ? OR ABS(cash_per_share - ?) < 0.000001)
                        """,
                        (
                            instrument["id"],
                            dividend.ex_date.isoformat(),
                            dividend.source,
                            dividend.cash_per_share,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO dividend_events
                        (instrument_id, ex_date, pay_date, record_date, cash_per_share, source, status, raw_json, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'announced', ?, CURRENT_TIMESTAMP)
                        """,
                        (
                            instrument["id"],
                            dividend.ex_date.isoformat(),
                            dividend.pay_date.isoformat() if dividend.pay_date else None,
                            dividend.record_date.isoformat() if dividend.record_date else None,
                            dividend.cash_per_share,
                            dividend.source,
                            json.dumps(dividend.payload or {}, ensure_ascii=False),
                        ),
                    )
            connection.commit()

    messages = []
    if quote is not None:
        messages.append("行情已更新")
    elif quote_error:
        messages.append(f"行情失败: {quote_error}")

    if dividends_loaded:
        messages.append(f"分红 {len(dividends)} 条")
    elif dividend_error:
        messages.append(f"分红失败: {dividend_error}")

    if quote is not None and dividends_loaded:
        status = "ok"
    elif quote is not None or dividends_loaded:
        status = "partial"
    else:
        status = "failed"

    return {"code": code, "status": status, "message": "; ".join(messages)}


def mark_job(state: AppState, job_id: int, status: str, message: str) -> None:
    with state.connection() as connection:
        connection.execute(
            "UPDATE refresh_jobs SET finished_at = CURRENT_TIMESTAMP, status = ?, message = ? WHERE id = ?",
            (status, message, job_id),
        )
        connection.commit()


def group_amounts(
    items: list[dict[str, Any]], key: str, amount_key: str, fallback_key: str | None = None
) -> list[dict[str, Any]]:
    grouped: dict[str, float] = {}
    for item in items:
        label = str(item.get(key) or item.get(fallback_key or key) or "未分类")
        grouped[label] = grouped.get(label, 0.0) + float(item.get(amount_key) or 0)
    return [
        {"label": label, "value": round(value, 2)}
        for label, value in sorted(grouped.items(), key=lambda pair: pair[1], reverse=True)
        if value > 0
    ]


def instrument_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "code": row["code"],
        "name": row.get("name") or "",
        "securityType": row.get("security_type") or row.get("securityType"),
        "exchange": row.get("exchange") or "",
        "industry": row.get("industry") or "",
    }


def transaction_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "instrumentId": row["instrument_id"],
        "code": row["code"],
        "name": row.get("name") or row["code"],
        "securityType": row.get("security_type"),
        "side": row["side"],
        "tradeDate": row["trade_date"],
        "quantity": row["quantity"],
        "price": row["price"],
        "fees": row["fees"],
        "note": row.get("note") or "",
    }


def dividend_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "instrumentId": row["instrument_id"],
        "code": row["code"],
        "name": row.get("name") or row["code"],
        "securityType": row.get("security_type"),
        "exDate": row["ex_date"],
        "payDate": row.get("pay_date"),
        "recordDate": row.get("record_date"),
        "cashPerShare": row["cash_per_share"],
        "source": row.get("source") or "",
        "status": row.get("status") or "announced",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--db", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = create_app(args.db, args.token)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
