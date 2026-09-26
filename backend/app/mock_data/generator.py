import random
from datetime import datetime, timedelta, timezone
from typing import TypeVar
from uuid import uuid4

from app.models.transaction import PaymentStatus, PaymentTransaction

MERCHANTS = [
    ("Cedar Shop", (8.0, 95.0), 0.24),
    ("Atlas Market", (25.0, 260.0), 0.18),
    ("Horizon Mart", (12.0, 150.0), 0.18),
    ("Urban Basket", (10.0, 130.0), 0.14),
    ("Nova Retail", (30.0, 320.0), 0.12),
    ("Bluebird Fuel", (20.0, 180.0), 0.06),
    ("WellSpring Pharmacy", (8.0, 110.0), 0.05),
    ("SkyLink Travel", (90.0, 850.0), 0.03),
]

REGIONS = [
    ("Tunis", 0.26),
    ("Sfax", 0.16),
    ("Sousse", 0.13),
    ("Ariana", 0.1),
    ("Nabeul", 0.09),
    ("Monastir", 0.07),
    ("Bizerte", 0.06),
    ("Gabes", 0.05),
    ("Kairouan", 0.04),
    ("Djerba", 0.04),
]

STATUSES: list[tuple[PaymentStatus, float]] = [
    ("approved", 0.91),
    ("declined", 0.055),
    ("pending", 0.02),
    ("reversed", 0.015),
]

CARD_SCHEMES = ["VISA", "Mastercard", "CIB", "E-Dinar"]
Choice = TypeVar("Choice")


def _weighted_choice(items: list[tuple[Choice, float]]) -> Choice:
    names = [item[0] for item in items]
    weights = [item[1] for item in items]
    return random.choices(names, weights=weights, k=1)[0]


def generate_transaction(timestamp: datetime | None = None) -> PaymentTransaction:
    merchant_name = _weighted_choice([(name, weight) for name, _range, weight in MERCHANTS])
    amount_min, amount_max = next(amount_range for name, amount_range, _weight in MERCHANTS if name == merchant_name)
    region = _weighted_choice(REGIONS)
    status = _weighted_choice(STATUSES)

    amount = round(random.uniform(amount_min, amount_max), 3)
    if status == "reversed":
        amount = round(amount * random.uniform(0.3, 1.0), 3)

    terminal_region_code = region[:3].upper()
    return PaymentTransaction(
        transaction_id=f"TX-{uuid4().hex[:12].upper()}",
        terminal_id=f"TPE-{terminal_region_code}-{random.randint(1000, 9999)}",
        merchant=merchant_name,
        region=region,
        amount=amount,
        timestamp=timestamp or datetime.now(timezone.utc),
        payment_status=status,
        card_scheme=random.choice(CARD_SCHEMES),
    )


def generate_historical_transactions(count: int) -> list[PaymentTransaction]:
    now = datetime.now(timezone.utc)
    transactions = []
    for index in range(count):
        minutes_ago = random.randint(0, 29)
        seconds_ago = random.randint(0, 59)
        timestamp = now - timedelta(minutes=minutes_ago, seconds=seconds_ago, milliseconds=index)
        transactions.append(generate_transaction(timestamp=timestamp))
    return sorted(transactions, key=lambda tx: tx.timestamp)
