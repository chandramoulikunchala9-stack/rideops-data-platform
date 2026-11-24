# Runbook: rotate the Lens master key (KEK)

Connection passwords and channel secrets are encrypted with a per-secret data key (DEK), and each DEK
is wrapped by a master key (KEK) from `LENS_MASTER_KEYS` (design §18.4). Rotation re-wraps the DEKs
only; the secrets themselves are not re-encrypted.

**When:** on a schedule (e.g. yearly), when someone with access to the key leaves, or after any
suspected exposure of the environment variables.

## Steps
1. Generate a new key locally (never paste it into chat or tickets):
   ```bash
   python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
   ```
2. Add it as the next version, keeping the old one: `LENS_MASTER_KEYS=1:<old>,2:<new>`.
   The highest version becomes active for new secrets. Deploy; `/readyz` must report `"keys": "ok"`.
3. Re-wrap everything onto version 2:
   ```bash
   python -m rideops_api rotate-keys     # logs "… 0 secrets still on old key versions"
   ```
   (or enqueue the `kek_rewrap` job).
4. Remove the old version: `LENS_MASTER_KEYS=2:<new>` and deploy.
5. Check: re-test one connection and send a test notification on one channel.

## If something goes wrong
* `/readyz` shows `keys: …` errors → the variable is malformed (each entry `version:base64`, 32 bytes).
* A secret fails to decrypt after step 4 → step 3 didn't finish; put the old version back, re-run
  `rotate-keys`, then remove it again.
* A lost key cannot be recovered: affected connections need their credentials entered again
  (**Rotate credentials**), and channels must be recreated.
