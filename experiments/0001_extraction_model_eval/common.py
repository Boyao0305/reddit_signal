"""Shared helpers: paths, read-only DB connection, OpenRouter key lookup."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DATA = HERE / "data"
OUTPUTS = DATA / "outputs"
SAMPLE_PATH = DATA / "sample.jsonl"

BUDGET_USD = 8.0  # hard cap for all LLM calls in this experiment


def dsn() -> str:
    value = os.environ.get("RS_RO_DATABASE_URL", "").strip()
    if not value:
        env_file = Path.home() / ".reddit_signal" / "readonly.env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("RS_RO_DATABASE_URL="):
                    value = line.split("=", 1)[1].strip()
    if not value:
        raise SystemExit("RS_RO_DATABASE_URL not set (expected in ~/.reddit_signal/readonly.env)")
    return value


def connect() -> psycopg.Connection:
    return psycopg.connect(dsn(), connect_timeout=15)


def openrouter_key() -> str:
    """Read the key from system_settings through the read-only role. Never log it."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM system_settings WHERE key = 'OPENROUTER_API_KEY'")
        row = cur.fetchone()
    if not row or not row[0]:
        raise SystemExit("OPENROUTER_API_KEY missing from system_settings")
    return row[0].strip()


def model_slug(model: str, tag: str = "") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", model)
    return f"{slug}__{tag}" if tag else slug


def output_path(model: str, tag: str = "") -> Path:
    return OUTPUTS / f"{model_slug(model, tag)}.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def total_spent() -> float:
    spent = 0.0
    for path in OUTPUTS.glob("*.jsonl"):
        for row in read_jsonl(path):
            spent += float(row.get("cost_usd") or 0.0)
    return spent
