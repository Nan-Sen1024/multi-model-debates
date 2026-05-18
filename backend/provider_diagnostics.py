from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import aiosqlite

from .database import DB_PATH, init_db

MAX_PROVIDER_DIAGNOSTIC_HISTORY = 10


def _normalize_history_item(item: Dict[str, Any], default_checked_at: int) -> Dict[str, Any]:
    item_checked_at = item.get("checked_at")
    if not isinstance(item_checked_at, int):
        item_checked_at = default_checked_at
    return {
        "status": item.get("status"),
        "code": item.get("code"),
        "summary": item.get("summary"),
        "message": item.get("message"),
        "checked_at": item_checked_at,
    }


def _derive_failure_summary(payload: Dict[str, Any]) -> Optional[str]:
    code = payload.get("code")
    fallback_target = payload.get("fallback_provider_name") or payload.get("fallback_provider_id")
    summary = payload.get("summary")
    if fallback_target:
        if code == "AUTHENTICATION_REQUIRED":
            return "主路鉴权失败"
        return "主路失败"
    return summary


def _build_implicit_history(payload: Dict[str, Any], checked_at: int) -> List[Dict[str, Any]]:
    if not (payload.get("summary") or payload.get("message") or payload.get("code")):
        return []

    if payload.get("healthy"):
        return [
            {
                "status": payload.get("status") or "healthy",
                "code": payload.get("code"),
                "summary": payload.get("summary"),
                "message": payload.get("message"),
                "checked_at": checked_at,
            }
        ]

    fallback_target = payload.get("fallback_provider_name") or payload.get("fallback_provider_id")
    if fallback_target:
        return [
            {
                "status": "failed",
                "code": payload.get("code"),
                "summary": _derive_failure_summary(payload),
                "message": payload.get("message"),
                "checked_at": checked_at,
            },
            {
                "status": "fallback_active",
                "code": payload.get("code"),
                "summary": f"已切换到 {fallback_target}",
                "message": "Fallback 接管流量",
                "checked_at": checked_at,
            },
        ]

    return [
        {
            "status": payload.get("status") or "failed",
            "code": payload.get("code"),
            "summary": payload.get("summary"),
            "message": payload.get("message"),
            "checked_at": checked_at,
        }
    ]


def _normalize_history(history: Any, checked_at: int) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(history, list):
        return None
    normalized_history: List[Dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        normalized_history.append(_normalize_history_item(item, checked_at))
    return normalized_history


def _load_persisted_diagnostic(raw_payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_payload, dict):
        return None
    return normalize_provider_diagnostic(raw_payload)


def _is_recovery(existing: Optional[Dict[str, Any]], current: Dict[str, Any]) -> bool:
    if existing is None or current.get("healthy") is not True:
        return False
    if existing.get("healthy") is False:
        return True
    return bool(existing.get("fallback_provider_id") or existing.get("fallback_provider_name"))


def _merge_provider_diagnostic(
    existing: Optional[Dict[str, Any]],
    current: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(current)
    if merged.get("healthy") is True:
        merged["fallback_provider_id"] = None
        merged["fallback_provider_name"] = None

    history: List[Dict[str, Any]] = []
    if existing and isinstance(existing.get("history"), list):
        history.extend(existing["history"])

    current_history = list(merged.get("history") or [])
    if _is_recovery(existing, merged):
        merged["code"] = None
        merged["summary"] = "主路恢复"
        current_history = [
            {
                "status": "recovered",
                "code": None,
                "summary": merged["summary"],
                "message": merged.get("message"),
                "checked_at": merged["checked_at"],
            }
        ]

    history.extend(current_history)
    merged["history"] = history[-MAX_PROVIDER_DIAGNOSTIC_HISTORY:] if history else None
    return merged


def normalize_provider_diagnostic(payload: Dict[str, Any]) -> Dict[str, Any]:
    checked_at = payload.get("checked_at")
    if not isinstance(checked_at, int):
        checked_at = int(time.time())
    history = payload.get("history")
    normalized_history = _normalize_history(history, checked_at)
    if normalized_history is None:
        implicit_history = _build_implicit_history(payload, checked_at)
        normalized_history = implicit_history or None
    return {
        "healthy": bool(payload.get("healthy")),
        "code": payload.get("code"),
        "summary": payload.get("summary"),
        "message": payload.get("message"),
        "checked_at": checked_at,
        "source": payload.get("source"),
        "fallback_provider_id": payload.get("fallback_provider_id"),
        "fallback_provider_name": payload.get("fallback_provider_name"),
        "history": normalized_history,
    }


async def persist_provider_diagnostic(
    provider_id: str,
    payload: Dict[str, Any],
    *,
    db_path: str = DB_PATH,
) -> Optional[Dict[str, Any]]:
    await init_db(db_path)
    current = normalize_provider_diagnostic(payload)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, last_diagnostic FROM provider_configs WHERE id = ? LIMIT 1",
            (provider_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        existing = None
        if row["last_diagnostic"]:
            try:
                existing = _load_persisted_diagnostic(json.loads(row["last_diagnostic"]))
            except json.JSONDecodeError:
                existing = None
        diagnostic = _merge_provider_diagnostic(existing, current)
        await db.execute(
            "UPDATE provider_configs SET last_diagnostic = ? WHERE id = ?",
            (json.dumps(diagnostic, ensure_ascii=False), provider_id),
        )
        await db.commit()
    return diagnostic
