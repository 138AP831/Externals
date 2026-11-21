"""Spotify tools and sign-in routes against a mocked Spotify API."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.tools import spotify_tools as sp

API = "https://api.spotify.com/v1"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "refresh-env")
    sp._access.update(token="", expires=0.0)
    yield
    sp._access.update(token="", expires=0.0)


def _token_route(mock, **extra):
    return mock.post(sp.TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "acc", "expires_in": 3600, **extra}
        )
    )


TRACK = {
    "type": "track",
    "name": "Blinding Lights",
    "uri": "spotify:track:abc",
    "duration_ms": 200000,
    "artists": [{"name": "The Weeknd"}],
    "album": {"name": "After Hours"},
}


@respx.mock
def test_now_playing_refreshes_token_once():
    tok = _token_route(respx)
    player = respx.get(f"{API}/me/player").mock(
        return_value=httpx.Response(
            200,
            json={
                "is_playing": True,
                "progress_ms": 65000,
                "item": TRACK,
                "device": {"name": "Galaxy A56"},
            },
        )
    )
    tool = sp.SpotifyNowPlaying()
    r1 = tool.execute()
    r2 = tool.execute()
    assert r1.success
    assert "Playing: Blinding Lights — The Weeknd" in r1.content
    assert "Galaxy A56" in r1.content and "1:05 / 3:20" in r1.content
    assert r2.success
    assert tok.call_count == 1  # cached access token
    assert player.calls[0].request.headers["Authorization"] == "Bearer acc"
    body = tok.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in body and "refresh-env" in body


@respx.mock
def test_nothing_playing():
    _token_route(respx)
    respx.get(f"{API}/me/player").mock(return_value=httpx.Response(204))
    assert "Nothing is playing" in sp.SpotifyNowPlaying().execute().content


@respx.mock
def test_play_searches_and_targets_phone_when_no_active_device():
    _token_route(respx)
    respx.get(f"{API}/search").mock(
        return_value=httpx.Response(200, json={"tracks": {"items": [TRACK]}})
    )
    respx.get(f"{API}/me/player").mock(return_value=httpx.Response(204))
    respx.get(f"{API}/me/player/devices").mock(
        return_value=httpx.Response(
            200,
            json={
                "devices": [
                    {"id": "pc", "type": "Computer"},
                    {"id": "phone", "type": "Smartphone"},
                ]
            },
        )
    )
    play = respx.put(f"{API}/me/player/play").mock(return_value=httpx.Response(204))
    res = sp.SpotifyPlay().execute(query="blinding lights")
    assert res.success, res.content
    assert "Playing Blinding Lights" in res.content
    req = play.calls[0].request
    assert req.url.params["device_id"] == "phone"
    assert b'"uris":["spotify:track:abc"]' in req.content.replace(b" ", b"")


@respx.mock
def test_playlist_uses_context_uri():
    _token_route(respx)
    respx.get(f"{API}/me/player").mock(
        return_value=httpx.Response(200, json={"device": {"is_active": True}})
    )
    play = respx.put(f"{API}/me/player/play").mock(return_value=httpx.Response(204))
    sp.SpotifyPlay().execute(uri="spotify:playlist:xyz")
    assert b"context_uri" in play.calls[0].request.content


@respx.mock
def test_premium_and_no_device_errors_are_friendly():
    _token_route(respx)
    respx.put(f"{API}/me/player/pause").mock(
        return_value=httpx.Response(
            403,
            json={
                "error": {"status": 403, "message": "x", "reason": "PREMIUM_REQUIRED"}
            },
        )
    )
    res = sp.SpotifyControl().execute(action="pause")
    assert not res.success and "Premium" in res.content

    respx.get(f"{API}/me/player").mock(return_value=httpx.Response(204))
    respx.get(f"{API}/me/player/devices").mock(
        return_value=httpx.Response(200, json={"devices": []})
    )
    res = sp.SpotifyControl().execute(action="resume")
    assert not res.success and "Open Spotify" in res.content


@respx.mock
def test_control_volume_clamped():
    _token_route(respx)
    vol = respx.put(f"{API}/me/player/volume").mock(return_value=httpx.Response(204))
    res = sp.SpotifyControl().execute(action="volume", volume_percent=150)
    assert res.content == "Volume set to 100%."
    assert vol.calls[0].request.url.params["volume_percent"] == "100"


@respx.mock
def test_top_and_recent_and_playlists():
    _token_route(respx)
    respx.get(f"{API}/me/top/artists").mock(
        return_value=httpx.Response(
            200, json={"items": [{"name": "Arijit Singh", "genres": ["filmi"]}]}
        )
    )
    respx.get(f"{API}/me/player/recently-played").mock(
        return_value=httpx.Response(
            200, json={"items": [{"played_at": "2026-11-21T08:00:00Z", "track": TRACK}]}
        )
    )
    respx.get(f"{API}/me/playlists").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"name": "Chill", "tracks": {"total": 42}, "uri": "u"}]},
        )
    )
    assert (
        "1. Arijit Singh (filmi)"
        in sp.SpotifyTopItems().execute(kind="artists", time_range="short_term").content
    )
    assert (
        "2026-11-21 08:00 Blinding Lights"
        in sp.SpotifyRecentlyPlayed().execute().content
    )
    assert "Chill (42 tracks)" in sp.SpotifyPlaylists().execute().content


def test_not_connected(monkeypatch):
    monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN")
    res = sp.SpotifyNowPlaying().execute()
    assert not res.success and "not connected" in res.content


@respx.mock
def test_saved_login_beats_env_and_rotated_token_is_saved():
    sp.save_refresh_token("refresh-file")
    tok = _token_route(respx, refresh_token="refresh-rotated")
    respx.get(f"{API}/me/player").mock(return_value=httpx.Response(204))
    sp.SpotifyNowPlaying().execute()
    assert "refresh-file" in tok.calls[0].request.content.decode()
    assert sp._refresh_token() == "refresh-rotated"


# -- sign-in routes ---------------------------------------------------------


def _client(api_key="pw"):
    from openjarvis.server.spotify_routes import router

    app = FastAPI()
    app.state.api_key = api_key
    app.include_router(router)
    return TestClient(app, follow_redirects=False)


def test_login_requires_password_and_redirects_with_state(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://jarvis.example.com")
    c = _client()
    assert c.get("/spotify/login").status_code == 401
    assert c.get("/spotify/login?key=wrong").status_code == 401
    r = c.get("/spotify/login?key=pw")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["redirect_uri"] == ["https://jarvis.example.com/spotify/callback"]
    assert "user-modify-playback-state" in q["scope"][0]
    assert q["state"][0]


@respx.mock
def test_callback_rejects_unknown_state_and_saves_token(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://jarvis.example.com")
    monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN")
    c = _client()
    assert c.get("/spotify/callback?code=x&state=forged").status_code == 400

    state = parse_qs(
        urlparse(c.get("/spotify/login?key=pw").headers["location"]).query
    )["state"][0]
    ex = respx.post(sp.TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "a", "refresh_token": "new-refresh"}
        )
    )
    r = c.get(f"/spotify/callback?code=thecode&state={state}")
    assert r.status_code == 200
    assert "new-refresh" in r.text and "SPOTIFY_REFRESH_TOKEN" in r.text
    assert "code=thecode" in ex.calls[0].request.content.decode()
    assert sp._refresh_token() == "new-refresh"
    # State is single-use.
    assert c.get(f"/spotify/callback?code=thecode&state={state}").status_code == 400
