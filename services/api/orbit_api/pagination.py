"""Keyset pagination helpers. Cursors are opaque: urlsafe-base64(orjson([sort key tuple]))."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, TypeVar

import orjson
from fastapi import Query

from .problem import ProblemException

T = TypeVar("T")
DEFAULT_LIMIT = 50
MAX_LIMIT = 500

LimitQuery = Query(ge=1, le=MAX_LIMIT, description="page size (max 500)")
CursorQuery = Query(description="opaque cursor from a previous page's next_cursor")


def encode_cursor(key: Sequence[Any]) -> str:
    raw = orjson.dumps(list(key))
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[Any, ...] | None:
    if cursor is None or cursor == "":
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        key = orjson.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, orjson.JSONDecodeError, ValueError) as exc:
        raise ProblemException(400, "invalid cursor", type_="urn:orbit:problem:bad-cursor") from exc
    if not isinstance(key, list):
        raise ProblemException(400, "invalid cursor", type_="urn:orbit:problem:bad-cursor")
    return tuple(key)


def cursor_datetime(value: Any) -> datetime:
    """Cursor components are JSON scalars; timestamps travel as ISO strings."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ProblemException(400, "invalid cursor", type_="urn:orbit:problem:bad-cursor") from exc


def page[T](
    rows: Sequence[T], limit: int, key_of: Callable[[T], Sequence[Any]]
) -> tuple[list[T], str | None]:
    """Given `limit + 1` rows fetched in sort order, return the page and the next cursor (or None)."""
    items = list(rows[:limit])
    next_cursor = encode_cursor(key_of(items[-1])) if len(rows) > limit and items else None
    return items, next_cursor
