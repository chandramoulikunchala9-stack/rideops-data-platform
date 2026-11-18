"""Payments: derived from bookings. One booking can produce several payment rows.

    charge attempt 1 --failed--> charge attempt 2 --failed--> charge attempt 3 (final)
          \\--success           \\--success                  \\--success / failed

Also simulated:
  * late settlement: a successful charge recorded 1-3 days after the ride (late-arriving data);
  * refunds: a separate `refund` row a few days after a successful charge.

Cash is collected by the driver, so it never fails, never settles late and has no gateway retry.
"""

import random
from datetime import timedelta

from rideops.generators.common import EntityBatch, make_rng, parse_ts, to_iso
from rideops.generators.defects import DefectRates, Record, apply_defects
from rideops.generators.reference import PAYMENT_FAILURE_REASONS, PAYMENT_GATEWAYS

PAYMENT_FIELDS = (
    "payment_id", "booking_id", "customer_id", "transaction_type", "attempt_number", "amount",
    "currency", "method", "gateway", "status", "failure_reason", "processed_at",
)  # fmt: skip

P_ATTEMPT_FAILS = 0.06
MAX_ATTEMPTS = 3
P_LATE_SETTLEMENT = 0.02
P_REFUND = 0.01


def build_payments(bookings: list[Record], *, seed: int) -> list[Record]:
    """Create payment rows for every booking that charged money (fare_amount > 0).

    Expects *clean* bookings: call this before apply_booking_defects().
    """
    rng = make_rng(seed, "payments")
    payments: list[Record] = []

    def add(**fields) -> None:
        fields["payment_id"] = f"PAY-{len(payments) + 1:08d}"
        payments.append({name: fields.get(name) for name in PAYMENT_FIELDS})

    for b in bookings:
        if not b["fare_amount"] or b["status"] not in ("completed", "cancelled"):
            continue
        method = b["payment_method"]
        common = {
            "booking_id": b["booking_id"],
            "customer_id": b["customer_id"],
            "amount": b["fare_amount"],
            "currency": b["currency"],
            "method": method,
            "gateway": PAYMENT_GATEWAYS[method],
        }
        charged_at = parse_ts(b["dropoff_at"] or b["cancelled_at"])
        t = charged_at + timedelta(seconds=rng.randint(5, 120))
        paid_at = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if method != "cash" and rng.random() < P_ATTEMPT_FAILS:
                add(
                    **common,
                    transaction_type="charge",
                    attempt_number=attempt,
                    status="failed",
                    failure_reason=rng.choice(PAYMENT_FAILURE_REASONS),
                    processed_at=to_iso(t),
                )
                t += timedelta(minutes=rng.uniform(1, 10))  # customer retries a few minutes later
                continue
            if method != "cash" and rng.random() < P_LATE_SETTLEMENT:
                t += timedelta(days=rng.uniform(1, 3))  # late-arriving settlement record
            add(
                **common,
                transaction_type="charge",
                attempt_number=attempt,
                status="success",
                processed_at=to_iso(t),
            )
            paid_at = t
            break

        if paid_at is not None and method != "cash" and rng.random() < P_REFUND:
            add(
                **common,
                transaction_type="refund",
                attempt_number=1,
                status="success",
                processed_at=to_iso(paid_at + timedelta(days=rng.uniform(1, 5))),
            )
    return payments


def _corrupt_payment(record: Record, rng: random.Random) -> None:
    choice = rng.randrange(3)
    if choice == 0:
        record["amount"] = -abs(record["amount"])  # sign belongs in transaction_type, not amount
    elif choice == 1:
        record["currency"] = "XYZ"  # unknown currency code
    else:
        record["booking_id"] = "BKG-9999999"  # orphan foreign key


def generate_payments(bookings: list[Record], *, seed: int, defects: DefectRates) -> EntityBatch:
    records = build_payments(bookings, seed=seed)
    counts = apply_defects(
        records,
        make_rng(seed, "payments-defects"),
        defects,
        corrupt=_corrupt_payment,
        nullable_fields=("method", "gateway"),
        mutate_duplicate=None,  # exact copies: a payment webhook delivered twice
    )
    return EntityBatch("payments", records, PAYMENT_FIELDS, "jsonl", counts)
