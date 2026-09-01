"""Dashboard-styled error handling for common FastAPI failures."""

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import dashboard_core as base


log = logging.getLogger(__name__)

ERROR_CSS = r'''
.error-shell{max-width:760px;margin:8vh auto 0}.error-card{padding:28px}.error-code{font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:#7890aa}.error-title{font-size:28px;margin:6px 0 10px}.error-message{font-size:14px;line-height:1.65;color:#aabbd0;max-width:650px}.error-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:22px}.error-detail{margin-top:18px;padding:12px 14px;border:1px solid rgba(116,153,196,.13);border-radius:10px;background:rgba(4,9,16,.35);color:#8298b1;font-size:12px}
'''


def _guild_id_from_path(path: str):
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "guild" and parts[1].isdigit():
        return int(parts[1])
    return None


def _error_copy(status_code: int, detail: str):
    if status_code == 400:
        return "Request could not be completed", detail or "The dashboard could not process that request."
    if status_code == 401:
        return "Sign in required", "Your dashboard session is missing or has expired. Sign in again to continue."
    if status_code == 403:
        return "Access denied", detail or "You do not have permission to access this dashboard resource."
    if status_code == 404:
        return "Not found", detail if detail and detail != "Not Found" else "The requested dashboard page or incident could not be found."
    if status_code == 409:
        return "Action unavailable", detail or "That action cannot be completed in the incident's current state."
    if status_code in (502, 503, 504):
        return "Service temporarily unavailable", "The dashboard could not reach a required service. Try again in a moment."
    return "Something went wrong", "The dashboard encountered an unexpected error while processing this request."


def _render_error(request: Request, status_code: int, detail: str = "", technical: str = ""):
    title, message = _error_copy(status_code, detail)
    guild_id = _guild_id_from_path(request.url.path)
    user = None
    try:
        user = base.current_user(request)
    except Exception:
        pass

    actions = []
    if status_code == 401:
        actions.append('<a class="btn" href="/login">Sign In</a>')
    elif guild_id:
        actions.append(f'<a class="btn" href="/guild/{guild_id}">Back to Overview</a>')
        actions.append(f'<a class="btn secondary" href="/guild/{guild_id}/history">Search History</a>')
    else:
        actions.append('<a class="btn" href="/">Back to Servers</a>')
    actions.append('<button class="btn secondary" type="button" onclick="location.reload()">Try Again</button>')

    tech = f'<div class="error-detail">{base.esc(technical)}</div>' if technical else ""
    body = f'''<div class="error-shell"><div class="card error-card"><div class="error-code">Dashboard error · {status_code}</div><h1 class="error-title">{base.esc(title)}</h1><div class="error-message">{base.esc(message)}</div>{tech}<div class="error-actions">{"".join(actions)}</div></div></div>'''
    try:
        response = base.page(f"{title} · Rescue Dispatch", body, user)
        markup = response.body.decode("utf-8").replace("</style>", ERROR_CSS + "\n</style>", 1)
        return HTMLResponse(markup, status_code=status_code)
    except Exception:
        # Keep a dependency-free fallback so an error in the dashboard shell does not
        # turn a recoverable failure into another exception.
        return HTMLResponse(
            f"<!doctype html><html><body><h1>{base.esc(title)}</h1><p>{base.esc(message)}</p><p><a href='/'>Back to dashboard</a></p></body></html>",
            status_code=status_code,
        )


@base.app.exception_handler(StarletteHTTPException)
async def dashboard_http_error(request: Request, exc: StarletteHTTPException):
    detail = str(exc.detail or "")
    return _render_error(request, exc.status_code, detail)


@base.app.exception_handler(RequestValidationError)
async def dashboard_validation_error(request: Request, exc: RequestValidationError):
    log.warning("Dashboard request validation failed for %s: %s", request.url.path, exc.errors())
    return _render_error(
        request,
        400,
        "The dashboard received an incomplete or invalid request.",
        "Check the form or link and try again.",
    )


@base.app.exception_handler(Exception)
async def dashboard_unexpected_error(request: Request, exc: Exception):
    log.exception("Unhandled dashboard error for %s", request.url.path, exc_info=exc)
    return _render_error(request, 500)


app = base.app
