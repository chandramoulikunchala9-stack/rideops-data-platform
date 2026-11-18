"""Static reference data shared by all generators: cities, vehicle types, loyalty tiers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    code: str
    name: str
    state_code: str  # used in vehicle registration and driving licence numbers
    weight: float  # relative share of platform activity in this city
    base_fare: float  # INR, used for booking fares in milestone 1b
    per_km_rate: float  # INR


CITIES: tuple[City, ...] = (
    City("BLR", "Bengaluru", "KA", 0.25, 55.0, 15.0),
    City("MUM", "Mumbai", "MH", 0.22, 60.0, 16.0),
    City("HYD", "Hyderabad", "TS", 0.20, 50.0, 14.0),
    City("DEL", "Delhi", "DL", 0.18, 50.0, 14.0),
    City("CHE", "Chennai", "TN", 0.08, 45.0, 13.0),
    City("PUN", "Pune", "MH", 0.07, 45.0, 13.0),
)
CITY_CODES: frozenset[str] = frozenset(c.code for c in CITIES)


@dataclass(frozen=True)
class VehicleType:
    name: str
    seats: int
    fare_multiplier: float
    weight: float
    models: tuple[tuple[str, str], ...]  # (make, model)


VEHICLE_TYPES: tuple[VehicleType, ...] = (
    VehicleType(
        "bike", 1, 0.5, 0.20, (("Honda", "Activa"), ("TVS", "Jupiter"), ("Bajaj", "Pulsar"))
    ),
    VehicleType("auto", 3, 0.7, 0.20, (("Bajaj", "RE"), ("Piaggio", "Ape"))),
    VehicleType(
        "mini", 4, 1.0, 0.30, (("Maruti Suzuki", "Swift"), ("Hyundai", "i10"), ("Tata", "Tiago"))
    ),
    VehicleType(
        "sedan", 4, 1.3, 0.20, (("Maruti Suzuki", "Dzire"), ("Honda", "Amaze"), ("Hyundai", "Aura"))
    ),
    VehicleType(
        "suv",
        6,
        1.7,
        0.10,
        (("Mahindra", "XUV700"), ("Toyota", "Innova Crysta"), ("Kia", "Carens")),
    ),
)

LOYALTY_TIERS: tuple[str, ...] = ("standard", "silver", "gold", "platinum")
LOYALTY_WEIGHTS: tuple[float, ...] = (0.70, 0.18, 0.09, 0.03)

DRIVER_STATUSES: tuple[str, ...] = ("active", "inactive", "suspended")
DRIVER_STATUS_WEIGHTS: tuple[float, ...] = (0.85, 0.10, 0.05)

# Reserved example domains (RFC 2606) so generated emails can never belong to a real person.
EMAIL_DOMAINS: tuple[str, ...] = ("example.com", "example.net", "example.org")

CITY_BY_CODE: dict[str, City] = {c.code: c for c in CITIES}
VEHICLE_TYPE_BY_NAME: dict[str, VehicleType] = {t.name: t for t in VEHICLE_TYPES}

# Relative ride demand for each hour of the day in local time (IST), index 0 = midnight.
# Morning (08-10) and evening (17-20) commuting peaks, quiet early hours.
HOUR_WEIGHTS_IST: tuple[float, ...] = (
    0.30, 0.20, 0.15, 0.10, 0.10, 0.20, 0.50, 1.20, 2.00, 2.20, 1.60, 1.20,
    1.10, 1.10, 1.00, 1.10, 1.40, 2.00, 2.40, 2.20, 1.70, 1.30, 0.90, 0.50,
)  # fmt: skip
PEAK_HOURS_IST: frozenset[int] = frozenset({8, 9, 17, 18, 19})
LATE_NIGHT_HOURS_IST: frozenset[int] = frozenset({22, 23, 0, 1})

BOOKING_STATUSES: tuple[str, ...] = ("completed", "cancelled", "no_driver_found", "in_progress")

PAYMENT_METHODS: tuple[str, ...] = ("upi", "card", "wallet", "cash")
PAYMENT_METHOD_WEIGHTS: tuple[float, ...] = (0.40, 0.35, 0.15, 0.10)
# Simulated gateway names: deliberately generic, not real payment companies.
PAYMENT_GATEWAYS: dict[str, str] = {
    "upi": "upi_gateway",
    "card": "card_gateway",
    "wallet": "wallet_gateway",
    "cash": "cash",
}
PAYMENT_FAILURE_REASONS: tuple[str, ...] = (
    "insufficient_funds",
    "card_declined",
    "upi_timeout",
    "bank_server_unavailable",
    "authentication_failed",
)

CANCEL_REASONS_CUSTOMER: tuple[str, ...] = (
    "changed_plans",
    "driver_too_far",
    "long_wait",
    "booked_another_ride",
    "wrong_pickup_location",
)
CANCEL_REASONS_DRIVER: tuple[str, ...] = (
    "customer_not_reachable",
    "customer_no_show",
    "vehicle_issue",
    "heavy_traffic",
)

# Approximate city-centre coordinates (lat, lon), used for event locations.
CITY_CENTERS: dict[str, tuple[float, float]] = {
    "BLR": (12.9716, 77.5946),
    "MUM": (19.0760, 72.8777),
    "HYD": (17.3850, 78.4867),
    "DEL": (28.6139, 77.2090),
    "CHE": (13.0827, 80.2707),
    "PUN": (18.5204, 73.8567),
}

APP_PLATFORMS: tuple[str, ...] = ("android", "ios", "web")
APP_PLATFORM_WEIGHTS: tuple[float, ...] = (0.70, 0.25, 0.05)
APP_VERSIONS: tuple[str, ...] = ("5.12.0", "5.13.1", "5.14.0", "5.14.2")


@dataclass(frozen=True)
class PipelineSpec:
    """A scheduled data pipeline. Names match the real pipelines built in later phases."""

    name: str
    layer: str  # bronze | silver | gold | serving | ops
    schedule_hour_utc: int
    baseline_minutes: float
    workers: int  # cluster size, drives simulated compute cost
    depends_on: tuple[str, ...] = ()


PIPELINES: tuple[PipelineSpec, ...] = (
    PipelineSpec("ingest_reference_data", "bronze", 0, 4, 2),
    PipelineSpec("ingest_bookings", "bronze", 0, 9, 4),
    PipelineSpec("ingest_payments", "bronze", 0, 7, 2),
    PipelineSpec("stream_ride_events", "bronze", 0, 14, 4),
    PipelineSpec("ingest_app_events", "bronze", 0, 8, 2),
    PipelineSpec("silver_transform", "silver", 1, 18, 6,
                 ("ingest_reference_data", "ingest_bookings", "ingest_payments",
                  "stream_ride_events", "ingest_app_events")),
    PipelineSpec("data_quality_checks", "silver", 1, 6, 2, ("silver_transform",)),
    PipelineSpec("gold_aggregations", "gold", 2, 12, 4, ("data_quality_checks",)),
    PipelineSpec("export_to_postgres", "serving", 2, 5, 1, ("gold_aggregations",)),
    PipelineSpec("ingest_cloud_costs", "ops", 3, 3, 1),
)  # fmt: skip
