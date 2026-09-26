import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import aiosqlite

from app.models.transaction import PaymentStatus, PaymentTransaction
from app.repositories.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS


class TransactionRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._path = self._sqlite_path(database_url)
        self._lock = asyncio.Lock()
        self._connection: aiosqlite.Connection | None = None

    @property
    def name(self) -> str:
        return "sqlite"

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._path)
        self._connection.row_factory = aiosqlite.Row
        try:
            await self._connection.execute("PRAGMA journal_mode=WAL")
            await self._connection.execute("PRAGMA foreign_keys=ON")
            await self._apply_migrations()
        except Exception:
            await self._connection.close()
            self._connection = None
            raise

    async def persist_batch(self, transactions: list[PaymentTransaction]) -> list[PaymentTransaction]:
        if not transactions:
            return []
        async with self._lock:
            connection = self._require_connection()
            inserted: list[PaymentTransaction] = []
            try:
                for tx in transactions:
                    cursor = await connection.execute(
                        """
                        INSERT INTO transactions (
                            transaction_id, terminal_id, merchant, region, amount,
                            timestamp, payment_status, card_scheme
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(transaction_id) DO NOTHING
                        """,
                        (
                            tx.transaction_id,
                            tx.terminal_id,
                            tx.merchant,
                            tx.region,
                            tx.amount,
                            tx.timestamp.isoformat(),
                            tx.payment_status,
                            tx.card_scheme,
                        ),
                    )
                    if cursor.rowcount != 1:
                        continue

                    inserted.append(tx)
                    approved_volume = tx.amount if tx.payment_status == "approved" else 0.0
                    minute = tx.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
                    await connection.execute(
                        """
                        INSERT INTO minute_aggregates (minute, transactions, approved_volume)
                        VALUES (?, 1, ?)
                        ON CONFLICT(minute) DO UPDATE SET
                            transactions = transactions + 1,
                            approved_volume = approved_volume + excluded.approved_volume
                        """,
                        (minute, approved_volume),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
            return inserted

    async def load_transactions(self) -> list[PaymentTransaction]:
        async with self._lock:
            cursor = await self._require_connection().execute(
                """
                SELECT transaction_id, terminal_id, merchant, region, amount,
                       timestamp, payment_status, card_scheme
                FROM transactions
                ORDER BY timestamp ASC, transaction_id ASC
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()

        return [
            PaymentTransaction(
                transaction_id=row["transaction_id"],
                terminal_id=row["terminal_id"],
                merchant=row["merchant"],
                region=row["region"],
                amount=row["amount"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                payment_status=cast(PaymentStatus, row["payment_status"]),
                card_scheme=row["card_scheme"],
            )
            for row in rows
        ]

    async def diagnostics(self) -> dict[str, str | int]:
        async with self._lock:
            connection = self._require_connection()
            tx_count = await self._scalar_count(connection, "transactions")
            minute_count = await self._scalar_count(connection, "minute_aggregates")
            schema_version = await self._current_schema_version(connection)
            return {
                "backend": self.name,
                "database_url": self.database_url,
                "transactions": tx_count,
                "minute_aggregates": minute_count,
                "schema_version": schema_version,
                "latest_schema_version": LATEST_SCHEMA_VERSION,
            }

    async def close(self) -> None:
        async with self._lock:
            if self._connection:
                await self._connection.close()
                self._connection = None

    async def _apply_migrations(self) -> None:
        connection = self._require_connection()
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        await connection.commit()

        current_version = await self._current_schema_version(connection)
        if current_version > LATEST_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current_version} is newer than supported version {LATEST_SCHEMA_VERSION}."
            )

        for migration in MIGRATIONS:
            if migration.version <= current_version:
                continue
            try:
                await connection.execute("BEGIN")
                for statement in migration.statements:
                    await connection.execute(statement)
                await connection.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, datetime.now(timezone.utc).isoformat()),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    @staticmethod
    async def _current_schema_version(connection: aiosqlite.Connection) -> int:
        cursor = await connection.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["version"] if row else 0)

    @staticmethod
    async def _scalar_count(connection: aiosqlite.Connection, table: str) -> int:
        if table not in {"transactions", "minute_aggregates"}:
            raise ValueError(f"Unsupported diagnostics table: {table}")
        cursor = await connection.execute(f"SELECT COUNT(*) AS count FROM {table}")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["count"] if row else 0)

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("TransactionRepository is not connected.")
        return self._connection

    @staticmethod
    def _sqlite_path(database_url: str) -> Path:
        if database_url.startswith("sqlite:///"):
            return Path(database_url.removeprefix("sqlite:///")).resolve()
        if database_url.startswith("sqlite://"):
            return Path(database_url.removeprefix("sqlite://")).resolve()
        return Path(database_url).resolve()
