from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from app.models import OfferObservation, PriceHistoryPoint


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    mode TEXT NOT NULL,
    input_file TEXT NOT NULL,
    products_count INTEGER NOT NULL DEFAULT 0,
    links_count INTEGER NOT NULL DEFAULT 0,
    observations_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    observed_at TEXT NOT NULL,
    product_id TEXT NOT NULL,
    store TEXT NOT NULL,
    seller TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    product_price_cents INTEGER,
    shipping_price_cents INTEGER,
    total_price_cents INTEGER,
    delivery_min_days INTEGER,
    delivery_max_days INTEGER,
    in_stock INTEGER NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_observations_product_time
ON observations(product_id, observed_at);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id),
    occurred_at TEXT NOT NULL,
    context TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id),
    attempted_at TEXT NOT NULL,
    product_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    event_type TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    previous_price_cents INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SENT', 'FAILED')),
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_notification_attempts_lookup
ON notification_attempts(product_id, channel, event_type, price_cents, status);
"""


def _to_cents(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int(value * 100)


def _from_cents(value: int | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value) / Decimal(100)


class RadarDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    def start_run(self, mode: str, input_file: str | Path) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (started_at, status, mode, input_file)
                VALUES (?, 'RUNNING', ?, ?)
                """,
                (datetime.now().astimezone().isoformat(), mode, str(input_file)),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        products_count: int = 0,
        links_count: int = 0,
        observations_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE runs
                SET finished_at = ?, status = ?, products_count = ?, links_count = ?,
                    observations_count = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    datetime.now().astimezone().isoformat(),
                    status,
                    products_count,
                    links_count,
                    observations_count,
                    error_message,
                    run_id,
                ),
            )

    def save_observations(
        self, run_id: int, observations: Iterable[OfferObservation]
    ) -> int:
        rows = []
        for observation in observations:
            rows.append(
                (
                    run_id,
                    observation.observed_at.isoformat(),
                    observation.product_id,
                    observation.store,
                    observation.seller,
                    observation.payment_method,
                    _to_cents(observation.product_price),
                    _to_cents(observation.shipping_price),
                    _to_cents(observation.total_price),
                    observation.delivery_min_days,
                    observation.delivery_max_days,
                    int(observation.in_stock),
                    observation.url,
                    observation.status,
                    observation.parser_version,
                    observation.error_message,
                )
            )
        with self.connection() as connection:
            connection.executemany(
                """
                INSERT INTO observations (
                    run_id, observed_at, product_id, store, seller, payment_method,
                    product_price_cents, shipping_price_cents, total_price_cents,
                    delivery_min_days, delivery_max_days, in_stock, url, status,
                    parser_version, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def historical_low(
        self,
        product_id: str,
        *,
        include_shipping: bool = True,
        mode: str | None = None,
        current_run_id: int | None = None,
    ) -> Decimal | None:
        price_column = "total_price_cents" if include_shipping else "product_price_cents"
        mode_clause = " AND r.mode = ?" if mode else ""
        run_clause = (
            " AND (r.status = 'SUCCESS' OR r.id = ?)"
            if current_run_id is not None
            else " AND r.status = 'SUCCESS'"
        )
        parameters: list[object] = [product_id]
        if mode:
            parameters.append(mode)
        if current_run_id is not None:
            parameters.append(current_run_id)
        with self.connection() as connection:
            row = connection.execute(
                f"""
                SELECT MIN(o.{price_column}) AS value
                FROM observations o
                JOIN runs r ON r.id = o.run_id
                WHERE o.product_id = ? AND o.status = 'OK' AND o.in_stock = 1
                    AND o.{price_column} IS NOT NULL{mode_clause}{run_clause}
                """,
                tuple(parameters),
            ).fetchone()
        return _from_cents(row["value"])

    def previous_best_total(
        self,
        product_id: str,
        current_run_id: int,
        *,
        include_shipping: bool = True,
        mode: str | None = None,
    ) -> Decimal | None:
        price_column = "total_price_cents" if include_shipping else "product_price_cents"
        mode_clause = " AND r2.mode = ?" if mode else ""
        parameters: tuple[object, ...] = (
            (product_id, product_id, current_run_id, mode)
            if mode
            else (product_id, product_id, current_run_id)
        )
        with self.connection() as connection:
            row = connection.execute(
                f"""
                SELECT MIN(o.{price_column}) AS value
                FROM observations o
                WHERE o.product_id = ? AND o.run_id = (
                    SELECT MAX(o2.run_id)
                    FROM observations o2
                    JOIN runs r2 ON r2.id = o2.run_id
                    WHERE o2.product_id = ? AND o2.run_id < ?
                        AND r2.status = 'SUCCESS'
                        AND o2.status = 'OK' AND o2.in_stock = 1
                        AND o2.{price_column} IS NOT NULL{mode_clause}
                )
                AND o.status = 'OK' AND o.in_stock = 1 AND o.{price_column} IS NOT NULL
                """,
                parameters,
            ).fetchone()
        return _from_cents(row["value"])

    def previous_product_stock_state(
        self,
        product_id: str,
        current_run_id: int,
        *,
        mode: str | None = None,
    ) -> bool | None:
        mode_clause = " AND r2.mode = ?" if mode else ""
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT o.run_id, o.in_stock, o.status
                FROM observations o
                JOIN runs r2 ON r2.id = o.run_id
                WHERE o.product_id = ? AND o.run_id < ?
                    AND r2.status = 'SUCCESS'{mode_clause}
                ORDER BY o.run_id DESC, o.id
                """,
                (
                    (product_id, current_run_id, mode)
                    if mode
                    else (product_id, current_run_id)
                ),
            ).fetchall()
        if not rows:
            return None

        current_previous_run_id: int | None = None
        run_rows: list[sqlite3.Row] = []
        for row in rows:
            if current_previous_run_id is None:
                current_previous_run_id = int(row["run_id"])
            if int(row["run_id"]) != current_previous_run_id:
                state = self._definitive_stock_state(run_rows)
                if state is not None:
                    return state
                run_rows = []
                current_previous_run_id = int(row["run_id"])
            run_rows.append(row)
        state = self._definitive_stock_state(run_rows)
        if state is not None:
            return state
        return None

    @staticmethod
    def _definitive_stock_state(rows: list[sqlite3.Row]) -> bool | None:
        if any(row["status"] == "OK" and bool(row["in_stock"]) for row in rows):
            return True
        if rows and all(row["status"] == "OUT_OF_STOCK" for row in rows):
            return False
        return None

    def price_history(
        self,
        product_id: str,
        *,
        include_shipping: bool = True,
        mode: str | None = None,
        current_run_id: int | None = None,
        by_store: bool = False,
    ) -> tuple[PriceHistoryPoint, ...]:
        price_column = "total_price_cents" if include_shipping else "product_price_cents"
        mode_clause = " AND r.mode = ?" if mode else ""
        current_clause = (
            " AND (r.status = 'SUCCESS' OR r.id = ?)"
            if current_run_id is not None
            else " AND r.status = 'SUCCESS'"
        )
        parameters: list[object] = [product_id]
        if mode:
            parameters.append(mode)
        if current_run_id is not None:
            parameters.append(current_run_id)
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT o.run_id, o.observed_at, o.store, o.payment_method,
                    o.delivery_max_days, o.{price_column} AS price_cents
                FROM observations o
                JOIN runs r ON r.id = o.run_id
                WHERE o.product_id = ? AND o.status = 'OK' AND o.in_stock = 1
                    AND o.{price_column} IS NOT NULL{mode_clause}{current_clause}
                ORDER BY o.run_id, o.{price_column},
                    COALESCE(o.delivery_max_days, 1000000), LOWER(o.store)
                """,
                tuple(parameters),
            ).fetchall()

        per_run: dict[object, PriceHistoryPoint] = {}
        per_store_run: dict[tuple[object, str], PriceHistoryPoint] = {}
        for row in rows:
            point = PriceHistoryPoint(
                observed_at=datetime.fromisoformat(row["observed_at"]),
                price=_from_cents(row["price_cents"]) or Decimal("0"),
                store=row["store"],
                payment_method=row["payment_method"],
            )
            if by_store:
                per_store_run.setdefault((row["run_id"], row["store"]), point)
            else:
                per_run.setdefault(row["run_id"], point)

        selected = per_store_run.values() if by_store else per_run.values()
        daily: dict[object, PriceHistoryPoint] = {}
        for point in selected:
            key: object = (
                (point.observed_at.date(), point.store)
                if by_store
                else point.observed_at.date()
            )
            daily[key] = point
        return tuple(
            sorted(
                daily.values(),
                key=lambda point: (point.observed_at, point.store.casefold()),
            )
        )

    def record_error(self, run_id: int | None, context: str, message: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO errors (run_id, occurred_at, context, message)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, datetime.now().astimezone().isoformat(), context, message),
            )

    def record_notification_attempt(
        self,
        run_id: int,
        product_id: str,
        price: Decimal,
        previous_price: Decimal,
        *,
        status: str,
        channel: str = "TELEGRAM",
        event_type: str = "NEW_HISTORICAL_LOW",
        error_message: str | None = None,
    ) -> None:
        if status not in {"SENT", "FAILED"}:
            raise ValueError("Status de notificacao deve ser SENT ou FAILED")
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO notification_attempts (
                    run_id, attempted_at, product_id, channel, event_type,
                    price_cents, previous_price_cents, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    datetime.now().astimezone().isoformat(),
                    product_id,
                    channel,
                    event_type,
                    _to_cents(price),
                    _to_cents(previous_price),
                    status,
                    error_message,
                ),
            )

    def notification_attempt_exists(
        self,
        product_id: str,
        price: Decimal,
        *,
        status: str,
        channel: str = "TELEGRAM",
        event_type: str = "NEW_HISTORICAL_LOW",
    ) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM notification_attempts
                WHERE product_id = ? AND channel = ? AND event_type = ?
                    AND price_cents = ? AND status = ?
                LIMIT 1
                """,
                (product_id, channel, event_type, _to_cents(price), status),
            ).fetchone()
        return row is not None

    def notification_sent_for_price(
        self,
        product_id: str,
        price: Decimal,
        *,
        channel: str = "TELEGRAM",
    ) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM notification_attempts
                WHERE product_id = ? AND channel = ? AND price_cents = ?
                    AND status = 'SENT'
                LIMIT 1
                """,
                (product_id, channel, _to_cents(price)),
            ).fetchone()
        return row is not None

    def pending_failed_notification(
        self,
        product_id: str,
        price: Decimal,
        *,
        channel: str = "TELEGRAM",
    ) -> tuple[str, Decimal] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT failed.event_type, failed.previous_price_cents
                FROM notification_attempts failed
                WHERE failed.product_id = ? AND failed.channel = ?
                    AND failed.price_cents = ? AND failed.status = 'FAILED'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM notification_attempts sent
                        WHERE sent.product_id = failed.product_id
                            AND sent.channel = failed.channel
                            AND sent.price_cents = failed.price_cents
                            AND sent.status = 'SENT'
                    )
                ORDER BY failed.id DESC
                LIMIT 1
                """,
                (product_id, channel, _to_cents(price)),
            ).fetchone()
        if row is None:
            return None
        return row["event_type"], _from_cents(row["previous_price_cents"])

    def failed_notification_previous_price(
        self,
        product_id: str,
        price: Decimal,
        *,
        channel: str = "TELEGRAM",
        event_type: str = "NEW_HISTORICAL_LOW",
    ) -> Decimal | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT previous_price_cents
                FROM notification_attempts
                WHERE product_id = ? AND channel = ? AND event_type = ?
                    AND price_cents = ? AND status = 'FAILED'
                ORDER BY id DESC
                LIMIT 1
                """,
                (product_id, channel, event_type, _to_cents(price)),
            ).fetchone()
        return _from_cents(row["previous_price_cents"]) if row else None
