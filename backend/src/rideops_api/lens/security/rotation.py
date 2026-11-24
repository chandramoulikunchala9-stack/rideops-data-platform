"""KEK rotation (§18.4, runbook lens-key-rotation.md): re-wrap every DEK with the active key.

1. Add a new version to LENS_MASTER_KEYS ("1:<old>,2:<new>"); the highest version becomes active.
2. Run `python -m rideops_api rotate-keys` (or let the `kek_rewrap` job run).
3. When it reports 0 remaining, remove the old version from LENS_MASTER_KEYS.
Only the small wrapped DEK changes; the secret ciphertext itself is never re-encrypted.
"""

import logging
from typing import Any

from rideops_api.jobs.registry import JobContext, job
from rideops_api.lens.alerts import channels
from rideops_api.lens.connections.service import keyring_for
from rideops_api.lens.security.crypto import SecretBlob, aad_for
from rideops_api.settings import get_lens_settings

logger = logging.getLogger(__name__)


def rewrap_all(db: Any, settings=None, batch: int = 500) -> dict[str, int]:
    settings = settings or get_lens_settings()
    ring = keyring_for(settings)
    done = {"connection_secrets": 0, "notification_channels": 0}
    with db.transaction() as conn:
        for r in conn.execute(
            """SELECT s.*, c.workspace_id FROM app.connection_secrets s JOIN app.connections c ON c.id = s.connection_id
                WHERE s.kek_version <> %s LIMIT %s FOR UPDATE OF s SKIP LOCKED""",
            (ring.active_version, batch),
        ).fetchall():
            old = SecretBlob(
                r["kek_version"], bytes(r["wrapped_dek"]), bytes(r["nonce"]), bytes(r["ciphertext"])
            )
            blob = ring.rewrap(old,
                               aad_for(r["workspace_id"], r["connection_id"]))  # fmt: skip
            conn.execute("UPDATE app.connection_secrets SET kek_version = %s, wrapped_dek = %s "
                         "WHERE connection_id = %s",
                         (blob.kek_version, blob.wrapped_dek, r["connection_id"]))  # fmt: skip
            done["connection_secrets"] += 1
        for r in conn.execute(
            """SELECT * FROM app.notification_channels WHERE config_enc IS NOT NULL AND kek_version <> %s
                LIMIT %s FOR UPDATE SKIP LOCKED""",
            (ring.active_version, batch),
        ).fetchall():
            old = SecretBlob(
                r["kek_version"], bytes(r["wrapped_dek"]), bytes(r["nonce"]), bytes(r["config_enc"])
            )
            blob = ring.rewrap(old,
                               channels.aad(r["workspace_id"], r["id"]))  # fmt: skip
            conn.execute("UPDATE app.notification_channels SET kek_version = %s, wrapped_dek = %s WHERE id = %s",
                         (blob.kek_version, blob.wrapped_dek, r["id"]))  # fmt: skip
            done["notification_channels"] += 1
    return done


def remaining(db: Any, settings=None) -> int:
    ring = keyring_for(settings or get_lens_settings())
    with db.pool.connection() as conn:
        return conn.execute(
            """SELECT (SELECT count(*) FROM app.connection_secrets WHERE kek_version <> %(v)s)
                    + (SELECT count(*) FROM app.notification_channels WHERE config_enc IS NOT NULL
                          AND kek_version <> %(v)s) AS n""",
            {"v": ring.active_version},
        ).fetchone()["n"]


@job("kek_rewrap", timeout_s=300, max_attempts=3, priority=20)
def kek_rewrap_job(ctx: JobContext) -> None:
    while True:
        done = rewrap_all(ctx.db, ctx.settings)
        logger.info("KEK re-wrap batch: %s", done)
        if not any(done.values()):
            break
