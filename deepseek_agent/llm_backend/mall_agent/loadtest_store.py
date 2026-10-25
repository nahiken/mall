"""File-backed store for published perf_test runs (see scripts/perf_test.py).

Runs are stored verbatim (enveloped) under ``<METRICS_DIR|logs>/loadtests/`` as
``<scenario-sanitized>_<received-ms>.json``. With the default METRICS_DIR
(``logs``) the directory is covered by the repo's root ``.gitignore`` ``logs/``
rule so published runs do not enter git; a custom METRICS_DIR would not be.
Only the most recent LOADTEST_KEEP runs are retained. Observability store — any
failure must surface as an empty list / raise to the caller (HTTP 400) and
never break the running agent.
"""
import json
import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger("mall_agent.loadtest_store")

LOADTEST_DIR = Path(os.getenv("METRICS_DIR", "logs")) / "loadtests"
LOADTEST_KEEP = int(os.getenv("LOADTEST_KEEP", "20"))
_must_have = {"scenario": str, "levels": list}


def publish(payload: dict) -> dict:
    """Validate + persist one perf_test run. Returns the stored envelope.

    Raises ValueError on a shape-invalid payload (caller maps to HTTP 400).
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    for key, kind in _must_have.items():
        if not isinstance(payload.get(key), kind):
            raise ValueError(f"payload.{key} must be {kind.__name__}")
    LOADTEST_DIR.mkdir(parents=True, exist_ok=True)
    scenario = re.sub(r"[^A-Za-z0-9._-]", "_", str(payload.get("scenario", "run")))[:80]
    received_at_ms = int(time.time() * 1000)
    envelope = {
        "id": f"{scenario}_{received_at_ms}",
        "received_at_ms": received_at_ms,
        "run": payload,
    }
    path = LOADTEST_DIR / f"{scenario}_{received_at_ms}.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    _trim()
    logger.info("loadtest run stored: %s (%d levels)", envelope["id"], len(payload.get("levels", [])))
    return envelope


def list_runs() -> list[dict]:
    """All stored envelopes, newest received first; unreadable files are skipped."""
    if not LOADTEST_DIR.is_dir():
        return []
    runs: list[dict] = []
    for path in LOADTEST_DIR.glob("*.json"):
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — a corrupt file must not kill the list
            logger.warning("loadtest store: skipping unreadable %s", path.name)
    runs.sort(key=lambda r: (isinstance(r, dict) and r.get("received_at_ms")) or 0, reverse=True)
    return runs


def _trim() -> None:
    """Keep only the newest LOADTEST_KEEP files."""
    files = sorted(LOADTEST_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[LOADTEST_KEEP:]:
        try:
            stale.unlink()
        except OSError:
            logger.warning("loadtest store: could not remove %s", stale.name)
