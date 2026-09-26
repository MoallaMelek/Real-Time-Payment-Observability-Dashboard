from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        version=1,
        name="initial_payment_schema",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id TEXT PRIMARY KEY,
                terminal_id TEXT NOT NULL,
                merchant TEXT NOT NULL,
                region TEXT NOT NULL,
                amount REAL NOT NULL,
                timestamp TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                card_scheme TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS minute_aggregates (
                minute TEXT PRIMARY KEY,
                transactions INTEGER NOT NULL DEFAULT 0,
                approved_volume REAL NOT NULL DEFAULT 0
            )
            """,
        ),
    ),
)


LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version
