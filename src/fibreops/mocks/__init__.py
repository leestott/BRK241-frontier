"""Shared helpers for reading seed data."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache
def load_json(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def load_text(name: str) -> str:
    return (DATA_DIR / name).read_text(encoding="utf-8")


def list_sops() -> list[dict[str, str]]:
    docs = []
    for path in sorted(DATA_DIR.glob("sop_*.md")):
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip()
        docs.append({"id": path.stem, "title": title, "text": text})
    return docs
