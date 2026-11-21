"""Spotify sign-in for a deployed Jarvis server.

``GET /spotify/login?key=<server password>`` sends the browser to Spotify;
Spotify redirects back to ``/spotify/callback``, which saves the refresh token
and shows it so it can be stored as ``SPOTIFY_REFRESH_TOKEN`` (needed on hosts
whose disk is wiped on restart).

These pages live outside ``/api`` because a browser navigation cannot send the
``Authorization`` header; the login page checks the server password itself
and the callback is bound to it by a one-time ``state`` value.
"""

from __future__ import annotations

import html
import os
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()

_STATE_TTL_SECONDS = 600
_pending_states: dict[str, float] = {}


def _redirect_uri(request: Request) -> str:
    explicit = os.environ.get("SPOTIFY_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    external = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if external:
        return f"{external}/spotify/callback"
    base = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto")
    if proto and base.startswith("http://") and proto == "https":
        base = "https://" + base[len("http://") :]
    return f"{base}/spotify/callback"


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;
line-height:1.5;color:#161618;background:#f7f7f8}}
@media (prefers-color-scheme:dark){{body{{color:#eee;background:#161618}}}}
textarea{{width:100%;font-family:monospace;font-size:13px;padding:8px;box-sizing:border-box}}
button,a.btn{{display:inline-block;margin-top:8px;padding:10px 16px;border-radius:10px;
border:0;background:#1db954;color:#fff;font-size:15px;text-decoration:none}}
code{{background:rgba(127,127,127,.15);padding:1px 4px;border-radius:4px}}
</style></head><body><h2>{html.escape(title)}</h2>{body}</body></html>"""
    return HTMLResponse(doc, status_code=status)


def _password_ok(request: Request, presented: str) -> bool:
    from openjarvis.server.auth_middleware import _api_keys_match

    expected = getattr(request.app.state, "api_key", "") or ""
    if not expected:
        return True  # keyless local server
    return bool(presented) and _api_keys_match(presented, expected)


@router.get("/spotify/login", include_in_schema=False)
async def spotify_login(request: Request, key: str = ""):
    from openjarvis.tools.spotify_tools import AUTH_URL, SCOPES, client_credentials

    if not _password_ok(request, key):
        return _page(
            "Spotify sign-in",
            "<p>Open this page with your Jarvis password: "
            "<code>/spotify/login?key=YOUR_PASSWORD</code></p>",
            status=401,
        )
    client_id, secret = client_credentials()
    if not client_id or not secret:
        return _page(
            "Spotify isn't set up yet",
            "<p>Add <code>SPOTIFY_CLIENT_ID</code> and "
            "<code>SPOTIFY_CLIENT_SECRET</code> "
            "to the server's environment, redeploy, then open this page again.</p>"
            f"<p>Redirect URI to register in your Spotify app: "
            f"<code>{html.escape(_redirect_uri(request))}</code></p>",
            status=400,
        )
    now = time.time()
    for s, t in list(_pending_states.items()):
        if now - t > _STATE_TTL_SECONDS:
            _pending_states.pop(s, None)
    state = secrets.token_urlsafe(24)
    _pending_states[state] = now
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(request),
        "scope": " ".join(SCOPES),
        "state": state,
        "show_dialog": "true",
    }
    return RedirectResponse(f"{AUTH_URL}?{urlencode(params)}", status_code=302)


@router.get("/spotify/callback", include_in_schema=False)
async def spotify_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
):
    from openjarvis.tools.spotify_tools import exchange_code, save_refresh_token

    issued = _pending_states.pop(state, None) if state else None
    if issued is None or time.time() - issued > _STATE_TTL_SECONDS:
        return _page(
            "Sign-in expired",
            "<p>This sign-in link is invalid or expired. Start again from "
            "<code>/spotify/login?key=YOUR_PASSWORD</code>.</p>",
            status=400,
        )
    if error or not code:
        return _page(
            "Spotify sign-in cancelled",
            f"<p>Spotify said: {html.escape(error or 'no code')}.</p>",
            status=400,
        )
    try:
        tokens = exchange_code(code, _redirect_uri(request))
    except Exception as exc:  # network or invalid client
        return _page(
            "Spotify sign-in failed",
            f"<p>{html.escape(str(exc))}</p><p>Check the client ID/secret and that "
            f"the redirect URI <code>{html.escape(_redirect_uri(request))}</code> is "
            "registered in your Spotify app.</p>",
            status=502,
        )
    refresh = str(tokens.get("refresh_token", ""))
    if not refresh:
        return _page("Spotify sign-in failed", "<p>No refresh token returned.</p>", 502)
    save_refresh_token(refresh)
    token_html = html.escape(refresh)
    return _page(
        "Spotify connected ✅",
        "<p>Jarvis can use your Spotify now. Ask things like "
        "<i>“what am I listening to?”</i> or <i>“play some lofi”</i>.</p>"
        "<p><b>Keep it after restarts:</b> the free server forgets this login when "
        "it restarts. In Render → <b>jarvis → Environment</b>, add "
        "<code>SPOTIFY_REFRESH_TOKEN</code> with this value, then save:</p>"
        f'<textarea id="t" rows="4" readonly>{token_html}</textarea>'
        '<button onclick="navigator.clipboard.writeText('
        "document.getElementById('t').value)"
        ".then(()=>this.textContent='Copied!')\">Copy</button>"
        '<p><a class="btn" href="/">Back to Jarvis</a></p>'
        "<p style='opacity:.7;font-size:13px'>Treat this value like a password.</p>",
    )
