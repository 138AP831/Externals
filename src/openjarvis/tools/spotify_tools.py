"""Spotify tools: what's playing, history, top items, search, playlists, and
playback control, via the Spotify Web API.

Auth: a Spotify developer app (``SPOTIFY_CLIENT_ID`` / ``SPOTIFY_CLIENT_SECRET``)
plus a long-lived refresh token, obtained once through the server's
``/spotify/login`` flow (see ``server/spotify_routes.py``). The refresh token is
read from the file that flow saves, falling back to ``SPOTIFY_REFRESH_TOKEN``
so it survives hosts whose disk is wiped on restart. Access tokens are
refreshed automatically.

Playback control (play/pause/skip/volume) needs Spotify Premium and an active
Spotify app (phone, desktop or speaker).
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
AUTH_URL = "https://accounts.spotify.com/authorize"
SCOPES = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "user-read-recently-played",
    "user-top-read",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
]

_lock = threading.Lock()
_access: Dict[str, Any] = {"token": "", "expires": 0.0}


class SpotifyNotConnected(RuntimeError):
    pass


class SpotifyAPIError(RuntimeError):
    def __init__(self, status: int, message: str, reason: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


def token_file() -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    home = os.environ.get("OPENJARVIS_HOME")
    base = Path(home) if home else DEFAULT_CONFIG_DIR
    return base / "connectors" / "spotify_server.json"


def client_credentials() -> Tuple[str, str]:
    return (
        os.environ.get("SPOTIFY_CLIENT_ID", "").strip(),
        os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip(),
    )


def _refresh_token() -> str:
    try:
        saved = json.loads(token_file().read_text(encoding="utf-8"))
        if saved.get("refresh_token"):
            return str(saved["refresh_token"])
    except (OSError, ValueError):
        pass
    return os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()


def _write_token_file(refresh_token: str) -> None:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"refresh_token": refresh_token}), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def save_refresh_token(refresh_token: str) -> None:
    """Persist a new login and drop any cached access token."""
    _write_token_file(refresh_token)
    with _lock:
        _access.update(token="", expires=0.0)


def _basic_auth() -> str:
    cid, secret = client_credentials()
    return base64.b64encode(f"{cid}:{secret}".encode()).decode()


def exchange_code(code: str, redirect_uri: str) -> Dict[str, Any]:
    """Trade an authorization code for tokens (used by the login callback)."""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Authorization": f"Basic {_basic_auth()}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def access_token() -> str:
    cid, secret = client_credentials()
    refresh = _refresh_token()
    if not (cid and secret and refresh):
        raise SpotifyNotConnected()
    with _lock:
        if _access["token"] and time.time() < _access["expires"] - 60:
            return str(_access["token"])
        resp = httpx.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh},
            headers={"Authorization": f"Basic {_basic_auth()}"},
            timeout=30.0,
        )
        if resp.status_code in (400, 401):
            raise SpotifyAPIError(
                resp.status_code,
                "Spotify rejected the saved login (it may have been revoked). "
                "Connect again at /spotify/login.",
            )
        resp.raise_for_status()
        data = resp.json()
        _access["token"] = data["access_token"]
        _access["expires"] = time.time() + float(data.get("expires_in", 3600))
        if data.get("refresh_token") and data["refresh_token"] != refresh:
            try:
                _write_token_file(data["refresh_token"])  # Spotify rotated it
            except OSError:
                pass
        return str(_access["token"])


def api(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Any:
    resp = httpx.request(
        method,
        f"{API}/{path.lstrip('/')}",
        headers={"Authorization": f"Bearer {access_token()}"},
        params={k: v for k, v in (params or {}).items() if v is not None},
        json=body,
        timeout=30.0,
    )
    if resp.status_code == 204 or not resp.content:
        return None
    if resp.status_code >= 400:
        reason, message = "", resp.text[:200]
        try:
            err = resp.json().get("error", {})
            reason = str(err.get("reason", ""))
            message = str(err.get("message", message))
        except ValueError:
            pass
        raise SpotifyAPIError(resp.status_code, message, reason)
    try:
        return resp.json()
    except ValueError:
        return None


_NOT_CONNECTED = (
    "Spotify is not connected. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET "
    "on the server, then open /spotify/login on the Jarvis site to sign in."
)


def _run(tool: str, fn) -> ToolResult:
    try:
        return ToolResult(tool_name=tool, content=fn())
    except SpotifyNotConnected:
        return ToolResult(tool_name=tool, content=_NOT_CONNECTED, success=False)
    except SpotifyAPIError as exc:
        if exc.reason == "NO_ACTIVE_DEVICE" or (
            exc.status == 404 and "device" in str(exc).lower()
        ):
            msg = (
                "No active Spotify device. Open Spotify on your phone, then try again."
            )
        elif exc.reason == "PREMIUM_REQUIRED" or exc.status == 403:
            msg = (
                "Spotify refused this action. Playback control needs Spotify "
                f"Premium. ({exc})"
            )
        else:
            msg = f"Spotify error {exc.status}: {exc}"
        return ToolResult(tool_name=tool, content=msg, success=False)
    except httpx.HTTPError as exc:
        return ToolResult(
            tool_name=tool, content=f"Spotify unreachable: {exc}", success=False
        )


def _artists(item: Dict[str, Any]) -> str:
    return ", ".join(a.get("name", "") for a in item.get("artists", []) or [])


def _track_line(t: Dict[str, Any]) -> str:
    album = (t.get("album") or {}).get("name", "")
    line = f"{t.get('name', '?')} — {_artists(t)}"
    return f"{line} (album: {album})" if album else line


def _limit(value: Any, default: int, maximum: int = 50) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, maximum))


class _SpotifyTool(BaseTool):
    is_local = False
    category = "media"

    def _spec(
        self, description: str, properties: Dict[str, Any], required=()
    ) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
            category="media",
            timeout_seconds=45.0,
        )


@ToolRegistry.register("spotify_now_playing")
class SpotifyNowPlaying(_SpotifyTool):
    tool_id = "spotify_now_playing"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "What is playing on the user's Spotify right now, and on which device.",
            {},
        )

    def execute(self, **params: Any) -> ToolResult:
        def fn() -> str:
            state = api("GET", "me/player", {"additional_types": "track,episode"})
            if not state or not state.get("item"):
                return "Nothing is playing on Spotify right now."
            item = state["item"]
            what = (
                _track_line(item)
                if item.get("type") == "track"
                else "{} ({})".format(
                    item.get("name"), (item.get("show") or {}).get("name", "podcast")
                )
            )
            device = (state.get("device") or {}).get("name", "unknown device")
            status = "Playing" if state.get("is_playing") else "Paused"
            pos = int(state.get("progress_ms") or 0) // 1000
            dur = int(item.get("duration_ms") or 0) // 1000
            return (
                f"{status}: {what} on {device} "
                f"[{pos // 60}:{pos % 60:02d} / {dur // 60}:{dur % 60:02d}]"
            )

        return _run(self.tool_id, fn)


@ToolRegistry.register("spotify_recently_played")
class SpotifyRecentlyPlayed(_SpotifyTool):
    tool_id = "spotify_recently_played"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Tracks the user recently listened to on Spotify, newest first.",
            {
                "limit": {
                    "type": "integer",
                    "description": "How many (default 10, max 50)",
                }
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        def fn() -> str:
            n = _limit(params.get("limit"), 10)
            data = api("GET", "me/player/recently-played", {"limit": n}) or {}
            items = data.get("items", [])
            if not items:
                return "No recently played tracks."
            lines = [
                "- {} {}".format(
                    i.get("played_at", "")[:16].replace("T", " "),
                    _track_line(i.get("track", {})),
                )
                for i in items
            ]
            return "Recently played:\n" + "\n".join(lines)

        return _run(self.tool_id, fn)


@ToolRegistry.register("spotify_top_items")
class SpotifyTopItems(_SpotifyTool):
    tool_id = "spotify_top_items"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "The user's most-listened Spotify tracks or artists.",
            {
                "kind": {"type": "string", "enum": ["tracks", "artists"]},
                "time_range": {
                    "type": "string",
                    "enum": ["short_term", "medium_term", "long_term"],
                    "description": (
                        "short_term ≈ 4 weeks, medium_term ≈ 6 months, "
                        "long_term ≈ years"
                    ),
                },
                "limit": {"type": "integer", "description": "How many (default 10)"},
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        def fn() -> str:
            kind = "artists" if params.get("kind") == "artists" else "tracks"
            rng = params.get("time_range") or "medium_term"
            if rng not in ("short_term", "medium_term", "long_term"):
                rng = "medium_term"
            n = _limit(params.get("limit"), 10)
            data = api("GET", f"me/top/{kind}", {"time_range": rng, "limit": n}) or {}
            items = data.get("items", [])
            if not items:
                return f"No top {kind} yet."
            if kind == "artists":
                lines = [
                    "{}. {} ({})".format(
                        i, a.get("name"), ", ".join(a.get("genres", [])[:3]) or "-"
                    )
                    for i, a in enumerate(items, 1)
                ]
            else:
                lines = [f"{i}. {_track_line(t)}" for i, t in enumerate(items, 1)]
            return f"Top {kind} ({rng}):\n" + "\n".join(lines)

        return _run(self.tool_id, fn)


def _search(query: str, kind: str, n: int) -> List[Dict[str, Any]]:
    data = api("GET", "search", {"q": query, "type": kind, "limit": n}) or {}
    return [i for i in (data.get(f"{kind}s") or {}).get("items", []) if i]


@ToolRegistry.register("spotify_search")
class SpotifySearch(_SpotifyTool):
    tool_id = "spotify_search"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Search Spotify for tracks, artists, albums or playlists.",
            {
                "query": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["track", "artist", "album", "playlist"],
                },
                "limit": {"type": "integer", "description": "How many (default 5)"},
            },
            required=["query"],
        )

    def execute(self, **params: Any) -> ToolResult:
        def fn() -> str:
            kind = params.get("kind") or "track"
            if kind not in ("track", "artist", "album", "playlist"):
                kind = "track"
            items = _search(
                str(params.get("query", "")), kind, _limit(params.get("limit"), 5, 20)
            )
            if not items:
                return "No results."
            lines = []
            for i in items:
                if kind == "track":
                    lines.append(f"- {_track_line(i)} [{i.get('uri')}]")
                elif kind == "playlist":
                    owner = (i.get("owner") or {}).get("display_name", "")
                    lines.append(f"- {i.get('name')} by {owner} [{i.get('uri')}]")
                elif kind == "album":
                    lines.append(f"- {i.get('name')} — {_artists(i)} [{i.get('uri')}]")
                else:
                    lines.append(f"- {i.get('name')} [{i.get('uri')}]")
            return f"Spotify {kind} results:\n" + "\n".join(lines)

        return _run(self.tool_id, fn)


@ToolRegistry.register("spotify_playlists")
class SpotifyPlaylists(_SpotifyTool):
    tool_id = "spotify_playlists"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "List the user's own and followed Spotify playlists.",
            {"limit": {"type": "integer", "description": "How many (default 20)"}},
        )

    def execute(self, **params: Any) -> ToolResult:
        def fn() -> str:
            data = (
                api("GET", "me/playlists", {"limit": _limit(params.get("limit"), 20)})
                or {}
            )
            items = [p for p in data.get("items", []) if p]
            if not items:
                return "No playlists."
            lines = [
                "- {} ({} tracks) [{}]".format(
                    p.get("name"),
                    (p.get("tracks") or {}).get("total", "?"),
                    p.get("uri"),
                )
                for p in items
            ]
            return "Playlists:\n" + "\n".join(lines)

        return _run(self.tool_id, fn)


def _ensure_device() -> Optional[str]:
    """Return a device id to target when nothing is active, else None."""
    state = api("GET", "me/player")
    if state and (state.get("device") or {}).get("is_active"):
        return None
    devices = (api("GET", "me/player/devices") or {}).get("devices", [])
    if not devices:
        raise SpotifyAPIError(404, "no device", "NO_ACTIVE_DEVICE")
    preferred = next((d for d in devices if d.get("type") == "Smartphone"), devices[0])
    return preferred.get("id")


@ToolRegistry.register("spotify_play")
class SpotifyPlay(_SpotifyTool):
    tool_id = "spotify_play"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Play music on the user's Spotify (needs Premium). Give `query` to "
            "search and play the best match (e.g. 'Blinding Lights', 'lofi beats "
            "playlist'), or a spotify `uri`. With neither, resumes playback.",
            {
                "query": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["track", "album", "playlist", "artist"],
                    "description": "What the query is (default track)",
                },
                "uri": {"type": "string", "description": "spotify:track:... etc."},
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        def fn() -> str:
            uri = str(params.get("uri") or "").strip()
            query = str(params.get("query") or "").strip()
            kind = params.get("kind") or "track"
            label = ""
            if not uri and query:
                if kind not in ("track", "album", "playlist", "artist"):
                    kind = "track"
                items = _search(query, kind, 1)
                if not items:
                    return f"Couldn't find '{query}' on Spotify."
                uri = items[0].get("uri", "")
                label = (
                    _track_line(items[0])
                    if kind == "track"
                    else f"{items[0].get('name')}"
                )
            body: Optional[Dict[str, Any]] = None
            if uri:
                body = (
                    {"uris": [uri]}
                    if ":track:" in uri or ":episode:" in uri
                    else {"context_uri": uri}
                )
            device = _ensure_device()
            api("PUT", "me/player/play", {"device_id": device}, body)
            return f"Playing {label or uri}." if (label or uri) else "Resumed playback."

        return _run(self.tool_id, fn)


@ToolRegistry.register("spotify_control")
class SpotifyControl(_SpotifyTool):
    tool_id = "spotify_control"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Control Spotify playback (needs Premium): pause, resume, next, "
            "previous, volume (0-100), shuffle on/off, repeat off/track/context.",
            {
                "action": {
                    "type": "string",
                    "enum": [
                        "pause",
                        "resume",
                        "next",
                        "previous",
                        "volume",
                        "shuffle_on",
                        "shuffle_off",
                        "repeat_off",
                        "repeat_track",
                        "repeat_context",
                    ],
                },
                "volume_percent": {
                    "type": "integer",
                    "description": "For action=volume",
                },
            },
            required=["action"],
        )

    def execute(self, **params: Any) -> ToolResult:
        def fn() -> str:
            action = str(params.get("action", "")).strip()
            if action == "pause":
                api("PUT", "me/player/pause")
                return "Paused."
            if action == "resume":
                api("PUT", "me/player/play", {"device_id": _ensure_device()})
                return "Resumed."
            if action == "next":
                api("POST", "me/player/next")
                return "Skipped to the next track."
            if action == "previous":
                api("POST", "me/player/previous")
                return "Went back to the previous track."
            if action == "volume":
                vol = max(0, min(100, int(params.get("volume_percent", 50))))
                api("PUT", "me/player/volume", {"volume_percent": vol})
                return f"Volume set to {vol}%."
            if action in ("shuffle_on", "shuffle_off"):
                api(
                    "PUT",
                    "me/player/shuffle",
                    {"state": str(action == "shuffle_on").lower()},
                )
                return f"Shuffle {'on' if action == 'shuffle_on' else 'off'}."
            if action.startswith("repeat_"):
                mode = action.split("_", 1)[1]
                api("PUT", "me/player/repeat", {"state": mode})
                return f"Repeat set to {mode}."
            return f"Unknown action '{action}'."

        return _run(self.tool_id, fn)
