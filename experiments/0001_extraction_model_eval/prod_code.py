"""Load the production prompt, prompt builders and parser verbatim from src/.

Importing ``src.pain_extraction.extraction`` directly would connect to the
database at import time (it reads the model id from runtime settings). Instead
the needed top-level definitions are extracted with ``ast`` from the source
files and executed in a private namespace, so the experiment runs the exact
production code without its side effects. Any rename in src/ fails loudly.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Optional

from common import REPO

SRC = REPO / "src" / "reddit_signal"
EXTRACTION_PY = SRC / "pain_extraction" / "extraction.py"


def _load(path: Path, names: set[str], namespace: dict) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    picked = []
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            picked.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & names:
                picked.append(node)
                found |= targets & names
    missing = names - found
    if missing:
        raise RuntimeError(f"{path}: definitions not found: {sorted(missing)}")
    module = ast.Module(body=picked, type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


_base_ns: dict[str, Any] = {
    "dataclass": dataclass, "field": field, "datetime": datetime,
    "Optional": Optional, "List": List, "Any": Any,
}
_models_ns = dict(_base_ns)
exec(compile((SRC / "pain_extraction" / "models.py").read_text(encoding="utf-8"),
             "models.py", "exec"), _models_ns)
PainObject = _models_ns["PainObject"]
PostWithComments = _models_ns["PostWithComments"]

_events_ns = _load(SRC / "sources" / "base.py", {"SignalEvent", "EventBundle"}, dict(_base_ns))
SignalEvent = _events_ns["SignalEvent"]
EventBundle = _events_ns["EventBundle"]

_extraction_ns = _load(
    EXTRACTION_PY,
    {"_SYSTEM_PROMPT", "_truncate", "build_prompt", "_build_generic_source_prompt", "parse_pain_json"},
    {
        "json": json, "re": re, "logger": logging.getLogger("prod_extraction"),
        "List": List, "Optional": Optional,
        "PainObject": PainObject, "PostWithComments": PostWithComments,
    },
)
SYSTEM_PROMPT: str = _extraction_ns["_SYSTEM_PROMPT"]
build_prompt: Callable = _extraction_ns["build_prompt"]
parse_pain_json: Callable = _extraction_ns["parse_pain_json"]

_converter_ns = {"PostWithComments": PostWithComments, "EventBundle": EventBundle}
CONVERTERS: dict[str, Callable] = {
    "reddit": _load(SRC / "pain_extraction" / "prompts" / "reddit.py",
                    {"bundle_to_postwithcomments"}, dict(_converter_ns))["bundle_to_postwithcomments"],
    "hackernews": _load(SRC / "pain_extraction" / "prompts" / "hackernews.py",
                        {"bundle_to_postwithcomments"}, dict(_converter_ns))["bundle_to_postwithcomments"],
}

# Production call parameters (src/pain_extraction/extraction.py::extract_for_post)
PROD_TEMPERATURE = 0.0
PROD_MAX_TOKENS = 2048


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


_capture = _Capture()
_prod_logger = logging.getLogger("prod_extraction")
_prod_logger.addHandler(_capture)
_prod_logger.propagate = False


def parse_with_status(raw: str, pwc: Any) -> tuple[list, str]:
    """Run the production parser; classify the outcome from its own warnings.

    Not thread-safe (shared capture handler) — call from one thread.
    Returns (pains, status) with status in {"ok", "json_error", "not_array"}.
    """
    _capture.messages.clear()
    pains = parse_pain_json(raw, pwc)
    status = "ok"
    for msg in _capture.messages:
        if msg.startswith("JSON parse error"):
            status = "json_error"
        elif msg.startswith("Expected JSON array"):
            status = "not_array"
    return pains, status


def event_from_dict(d: dict) -> Any:
    d = dict(d)
    d["event_time"] = datetime.fromisoformat(d["event_time"]) if d.get("event_time") else None
    return SignalEvent(**d)


def bundle_from_sample(row: dict) -> Any:
    return EventBundle(parent=event_from_dict(row["parent"]),
                       children=[event_from_dict(c) for c in row["children"]])


def pwc_from_sample(row: dict) -> Any:
    return CONVERTERS[row["source"]](bundle_from_sample(row))


def pain_to_dict(p: Any) -> dict:
    return {
        "pain_statement": p.pain_statement,
        "raw_quote": p.raw_quote,
        "mentioned_tools": list(p.mentioned_tools or []),
        "is_workaround": bool(p.is_workaround),
        "source_author": p.source_author,
        "comment_id": p.comment_id,
        "signal_event_id": p.signal_event_id,
    }
