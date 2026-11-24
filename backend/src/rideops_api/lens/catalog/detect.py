"""Heuristics run on every schema sync (§14.3): freshness column and PII columns."""

import re
from typing import Any

FRESHNESS_PRIORITY = ("updated_at", "modified_at", "last_updated", "last_modified", "updated", "created_at",
                      "inserted_at", "event_time", "event_ts", "ts", "timestamp")  # fmt: skip

# Column-name tokens that suggest personal or secret data (F-AN-12). Matched on whole words of the
# snake/camel-cased name, so `email_verified` (a boolean) is still flagged conservatively, while
# `campaign` does not match `pan`.
PII_TOKENS = {
    "email", "mail", "phone", "mobile", "msisdn", "ssn", "aadhaar", "aadhar", "pan", "passport", "dob",
    "birthdate", "birthday", "address", "street", "zip", "postcode", "postal", "name", "firstname",
    "lastname", "surname", "fullname", "card", "cc", "cvv", "iban", "account", "ip", "ipaddr",
    "password", "passwd", "pwd", "secret", "token", "apikey", "salary", "gender", "lat", "lng",
    "latitude", "longitude", "license", "licence", "vin", "upi", "gstin",
}  # fmt: skip
NOT_PII_NAMES = {"table_name", "schema_name", "file_name", "filename", "username_hash", "name_hash", "pipeline_name",
                 "city_name", "dataset_name", "job_name", "event_name", "status_name"}  # fmt: skip

_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokens(name: str) -> list[str]:
    return [t for t in _SPLIT.split(_CAMEL.sub("_", name).lower()) if t]


def is_pii_name(name: str) -> bool:
    if name.lower() in NOT_PII_NAMES:
        return False
    parts = tokens(name)
    joined = "".join(parts)
    return any(p in PII_TOKENS for p in parts) or joined in PII_TOKENS


def is_temporal(data_type: str) -> bool:
    t = data_type.lower()
    return t.startswith("timestamp") or t == "date" or t.startswith("datetime")


def freshness_column(table: dict[str, Any]) -> str | None:
    temporal = [c for c in table["columns"] if is_temporal(c["data_type"])]
    if not temporal:
        return None
    by_name = {c["name"].lower(): c["name"] for c in temporal}
    for preferred in FRESHNESS_PRIORITY:
        if preferred in by_name:
            return by_name[preferred]
    indexed = {i["columns"][0] for i in table.get("indexes", []) if i.get("columns")}
    for c in temporal:
        if c["name"] in indexed:
            return c["name"]
    if len(temporal) == 1:  # the only timestamp a table has is its best freshness signal
        return temporal[0]["name"]
    return None


def pii_columns(table: dict[str, Any]) -> list[str]:
    return [c["name"] for c in table["columns"] if is_pii_name(c["name"])]
