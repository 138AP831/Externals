"""POST /v1/vision: camera frames + question -> short answer."""

from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server.vision_routes import router

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 100).decode()


class FakeEngine:
    def __init__(self):
        self.calls = []

    def generate(self, messages, *, model, **kw):
        self.calls.append((messages, model))
        return {"content": "About half full and rising."}


def _client():
    app = FastAPI()
    app.state.engine = FakeEngine()
    app.state.model = "gemini-x"
    app.include_router(router)
    return TestClient(app), app.state.engine


def test_sends_frames_and_history_to_model():
    c, eng = _client()
    r = c.post(
        "/v1/vision",
        json={
            "question": "Is this enough water?",
            "images": [JPEG, "data:image/jpeg;base64," + JPEG],
            "history": [
                {"role": "user", "content": "what plant is this"},
                {"role": "assistant", "content": "A pothos."},
            ],
        },
    )
    assert r.status_code == 200
    assert r.json() == {"answer": "About half full and rising.", "model": "gemini-x"}
    messages, model = eng.calls[0]
    assert model == "gemini-x"
    assert messages[0].role.value == "system" and "camera" in messages[0].content
    assert [m.content for m in messages[1:3]] == ["what plant is this", "A pothos."]
    last = messages[-1]
    assert last.content == "Is this enough water?"
    assert last.images == [JPEG, JPEG]


def test_rejects_bad_input():
    c, _ = _client()
    assert c.post("/v1/vision", json={"images": []}).status_code == 400
    assert c.post("/v1/vision", json={"images": ["%%%"]}).status_code == 400
    gif = base64.b64encode(b"GIF89a....").decode()
    assert c.post("/v1/vision", json={"images": [gif]}).status_code == 400
    assert c.post("/v1/vision", json={"images": [JPEG] * 5}).status_code == 400
    big = base64.b64encode(b"\xff\xd8\xff" + b"0" * 1_600_000).decode()
    assert c.post("/v1/vision", json={"images": [big]}).status_code == 413
