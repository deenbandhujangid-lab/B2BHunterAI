"""Job scrape progress — resume after pause/restart without re-walking from A."""

from __future__ import annotations

import json
from typing import Any


def parse_checkpoint(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def get_role_checkpoint(checkpoint: dict[str, Any], role: str) -> dict[str, Any]:
    roles = checkpoint.get("roles") or {}
    return dict(roles.get(role) or {})


def set_role_checkpoint(
    checkpoint: dict[str, Any],
    role: str,
    *,
    category: str,
    company_index: int,
    company_name: str,
) -> dict[str, Any]:
    roles = dict(checkpoint.get("roles") or {})
    roles[role] = {
        "category": category,
        "company_index": company_index,
        "company_name": company_name,
    }
    return {"roles": roles}


def dump_checkpoint(checkpoint: dict[str, Any]) -> str:
    return json.dumps(checkpoint, ensure_ascii=False)
