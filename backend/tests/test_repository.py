import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.repositories.migrations import LATEST_SCHEMA_VERSION
from app.repositories.transaction_repository import TransactionRepository
from tests.helpers import make_transaction


class TransactionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_initializes_schema_and_reports_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = TransactionRepository(str(Path(directory) / "payments.db"))
            await repository.connect()
            diagnostics = await repository.diagnostics()
            await repository.close()

            self.assertEqual(diagnostics["schema_version"], LATEST_SCHEMA_VERSION)
            self.assertEqual(diagnostics["latest_schema_version"], LATEST_SCHEMA_VERSION)
            self.assertEqual(diagnostics["transactions"], 0)

    async def test_migrates_existing_schema_without_losing_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE transactions (
                    transaction_id TEXT PRIMARY KEY,
                    terminal_id TEXT NOT NULL,
                    merchant TEXT NOT NULL,
                    region TEXT NOT NULL,
                    amount REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    payment_status TEXT NOT NULL,
                    card_scheme TEXT NOT NULL
                );
                CREATE TABLE minute_aggregates (
                    minute TEXT PRIMARY KEY,
                    transactions INTEGER NOT NULL DEFAULT 0,
                    approved_volume REAL NOT NULL DEFAULT 0
                );
                INSERT INTO transactions VALUES (
                    'TX-LEGACY', 'TPE-TUN-1000', 'Cedar Shop', 'Tunis', 25.0,
                    '2026-06-15T08:30:00+00:00', 'approved', 'VISA'
                );
                """
            )
            connection.commit()
            connection.close()

            repository = TransactionRepository(str(path))
            await repository.connect()
            loaded = await repository.load_transactions()
            diagnostics = await repository.diagnostics()
            await repository.close()

            self.assertEqual([item.transaction_id for item in loaded], ["TX-LEGACY"])
            self.assertEqual(diagnostics["schema_version"], LATEST_SCHEMA_VERSION)

    async def test_mixed_batch_is_fully_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payments.db"
            repository = TransactionRepository(str(path))
            await repository.connect()

            first = make_transaction("TX-1")
            second = make_transaction("TX-2")
            self.assertEqual(await repository.persist_batch([first]), [first])
            inserted = await repository.persist_batch([first, second, second])
            diagnostics = await repository.diagnostics()
            await repository.close()

            self.assertEqual(inserted, [second])
            self.assertEqual(diagnostics["transactions"], 2)

            connection = sqlite3.connect(path)
            aggregate_count = connection.execute("SELECT SUM(transactions) FROM minute_aggregates").fetchone()[0]
            connection.close()
            self.assertEqual(aggregate_count, 2)
