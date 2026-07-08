#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Mapping, Optional


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKING_FIELDS = ("checked", "commented", "made_pr")
TRACKING_FIELD_LABELS = {
    "checked": "Checked",
    "commented": "Commented",
    "made_pr": "Made PR",
}


def get_tracking_path() -> str:
    return os.getenv(
        "GITHUB_ISSUE_TRACKING",
        os.path.join(BASE_DIR, "github_issue_tracking.json"),
    )


def get_tracking_host() -> str:
    return os.getenv("GITHUB_ISSUE_TRACKING_HOST", "127.0.0.1")


def get_tracking_port() -> int:
    try:
        return int(os.getenv("GITHUB_ISSUE_TRACKING_PORT", "8765"))
    except ValueError:
        return 8765


def get_tracking_api_base() -> str:
    return os.getenv(
        "GITHUB_ISSUE_TRACKING_API",
        f"http://{get_tracking_host()}:{get_tracking_port()}",
    ).rstrip("/")


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def issue_tracking_key(repo: Any, number: Any) -> str:
    repo_text = str(repo or "").strip()
    number_text = str(number or "").strip()
    if repo_text and number_text:
        return f"{repo_text}#{number_text}"
    return repo_text or number_text


def issue_key_from_item(item: Mapping[str, Any]) -> str:
    key = item.get("issue_key")
    if key:
        return str(key)
    return issue_tracking_key(item.get("repo"), item.get("number"))


def empty_tracking() -> Dict[str, Any]:
    return {"version": 1, "issues": {}}


def _normalize_entry(entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        entry = {}
    normalized = dict(entry)
    for field in TRACKING_FIELDS:
        normalized[field] = bool(entry.get(field, False))
    return normalized


def normalize_tracking(data: Any) -> Dict[str, Any]:
    normalized = empty_tracking()
    if not isinstance(data, dict):
        return normalized

    issues = data.get("issues")
    if not isinstance(issues, dict):
        # Backward-compatible shape: {"repo/name#123": {"checked": true}}
        issues = data

    for issue_key, entry in issues.items():
        key = str(issue_key).strip()
        if key:
            normalized["issues"][key] = _normalize_entry(entry)

    return normalized


def load_tracking(path: Optional[str] = None) -> Dict[str, Any]:
    tracking_path = path or get_tracking_path()
    try:
        with open(tracking_path, "r", encoding="utf-8") as f:
            return normalize_tracking(json.load(f))
    except FileNotFoundError:
        return empty_tracking()
    except json.JSONDecodeError:
        return empty_tracking()


def save_tracking(data: Mapping[str, Any], path: Optional[str] = None) -> None:
    tracking_path = path or get_tracking_path()
    parent = os.path.dirname(os.path.abspath(tracking_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=".github_issue_tracking.",
        suffix=".tmp",
        dir=parent or None,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(normalize_tracking(data), f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_path, tracking_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def apply_tracking_update(
    issue_key: str,
    field: str,
    value: bool,
    metadata: Optional[Mapping[str, Any]] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    clean_key = str(issue_key or "").strip()
    if not clean_key:
        raise ValueError("issue_key is required")
    if field not in TRACKING_FIELDS:
        raise ValueError(f"field must be one of: {', '.join(TRACKING_FIELDS)}")

    tracking = load_tracking(path)
    issues = tracking.setdefault("issues", {})
    entry = _normalize_entry(issues.get(clean_key, {}))
    now = utc_timestamp()

    if not entry.get("first_seen_at"):
        entry["first_seen_at"] = now
    entry[field] = bool(value)
    entry["updated_at"] = now
    entry[f"{field}_updated_at"] = now

    for key in ("repo", "number", "title", "url"):
        if metadata and metadata.get(key) not in (None, ""):
            entry[key] = metadata[key]

    issues[clean_key] = entry
    save_tracking(tracking, path)
    return entry
