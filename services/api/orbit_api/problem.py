"""RFC 7807 problem+json errors. Every error the API emits goes through here; stack traces never leak."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from opentelemetry import trace
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger("orbit_api.problem")
PROBLEM_MEDIA_TYPE = "application/problem+json"

_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    501: "Not Implemented",
    503: "Service Unavailable",
}


class ProblemException(StarletteHTTPException):
    """HTTPException that carries an RFC 7807 `type` URI and optional extension members."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        type_: str = "about:blank",
        title: str | None = None,
        headers: dict[str, str] | None = None,
        **extensions: Any,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.type_ = type_
        self.title = title
        self.extensions = extensions


def trace_id_for(request: Request) -> str:
    """Prefer the active OpenTelemetry trace id; fall back to the request id set by middleware."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return str(getattr(request.state, "request_id", "") or "")


def problem_response(
    request: Request,
    status: int,
    detail: str,
    *,
    type_: str = "about:blank",
    title: str | None = None,
    headers: dict[str, str] | None = None,
    **extensions: Any,
) -> ORJSONResponse:
    body: dict[str, Any] = {
        "type": type_,
        "title": title or _TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "trace_id": trace_id_for(request),
    }
    body.update(extensions)
    return ORJSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_MEDIA_TYPE)


async def _http_exception(request: Request, exc: Exception) -> ORJSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    type_ = getattr(exc, "type_", "about:blank")
    title = getattr(exc, "title", None)
    extensions = getattr(exc, "extensions", {})
    return problem_response(
        request,
        exc.status_code,
        detail,
        type_=type_,
        title=title,
        headers=dict(exc.headers or {}),
        **extensions,
    )


async def _validation_exception(request: Request, exc: Exception) -> ORJSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    detail = "; ".join(f"{'.'.join(e['loc'])}: {e['msg']}" for e in errors) or "validation failed"
    return problem_response(request, 422, detail, type_="urn:orbit:problem:validation", errors=errors)


async def _unhandled_exception(request: Request, exc: Exception) -> ORJSONResponse:
    log.exception("unhandled error", path=request.url.path, error=type(exc).__name__)
    return problem_response(
        request,
        500,
        "An unexpected error occurred.",
        type_="urn:orbit:problem:internal",
    )


def install(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_exception)
    app.add_exception_handler(Exception, _unhandled_exception)
