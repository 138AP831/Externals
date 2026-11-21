"""Camera questions: ``POST /v1/vision``.

The web app's camera view sends the user's question plus a few JPEG frames
captured over ~1–2 seconds (so motion such as pouring water is visible) and
gets a short, speakable answer from the server's vision-capable model.
Lives under ``/v1`` so the server password (API key) protects it.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openjarvis.core.types import Message, Role

router = APIRouter()

MAX_FRAMES = 4
MAX_FRAME_BYTES = 1_500_000  # decoded JPEG size per frame
MAX_HISTORY = 6

SYSTEM_PROMPT = (
    "You are Nova, a helpful assistant looking through the user's phone "
    "camera in real time. The images are consecutive frames from the live "
    "camera, oldest first, about {gap} seconds apart, so you can judge motion "
    "and change (for example how fast water is being poured). Answer the "
    "user's question about what the camera shows: be direct, practical and "
    "specific, in 1-4 short sentences suitable for reading aloud, with no "
    "markdown. If the view is unclear or you need a different angle, say "
    "exactly what to point at. Do not invent details you cannot see."
)


class VisionTurn(BaseModel):
    role: str
    content: str = ""


class VisionRequest(BaseModel):
    question: str = Field(default="What am I looking at?", max_length=2000)
    images: List[str] = Field(default_factory=list)
    frame_gap_seconds: float = 0.7
    history: List[VisionTurn] = Field(default_factory=list)
    model: Optional[str] = None


class VisionResponse(BaseModel):
    answer: str
    model: str


def _clean_frames(images: List[str]) -> List[str]:
    if not images:
        raise HTTPException(status_code=400, detail="At least one image is required")
    if len(images) > MAX_FRAMES:
        raise HTTPException(status_code=400, detail=f"At most {MAX_FRAMES} images")
    frames: List[str] = []
    for raw in images:
        data = raw.split(",", 1)[1] if raw.startswith("data:") else raw
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Images must be base64")
        if len(decoded) > MAX_FRAME_BYTES:
            raise HTTPException(status_code=413, detail="Image too large")
        if not (decoded[:3] == b"\xff\xd8\xff" or decoded[:8] == b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(status_code=400, detail="Images must be JPEG or PNG")
        frames.append(data)
    return frames


@router.post("/v1/vision", response_model=VisionResponse)
async def vision(req: VisionRequest, request: Request) -> VisionResponse:
    engine = getattr(request.app.state, "engine", None)
    model = req.model or getattr(request.app.state, "model", "") or ""
    if engine is None or not model:
        raise HTTPException(status_code=503, detail="No model available")
    frames = _clean_frames(req.images)

    gap = max(0.1, min(req.frame_gap_seconds, 5.0))
    messages: List[Message] = [
        Message(role=Role.SYSTEM, content=SYSTEM_PROMPT.format(gap=f"{gap:.1f}"))
    ]
    for turn in req.history[-MAX_HISTORY:]:
        role = Role.ASSISTANT if turn.role == "assistant" else Role.USER
        if turn.content.strip():
            messages.append(Message(role=role, content=turn.content[:2000]))
    question = req.question.strip() or "What am I looking at?"
    messages.append(Message(role=Role.USER, content=question, images=frames))

    try:
        result = await asyncio.to_thread(
            engine.generate, messages, model=model, temperature=0.4, max_tokens=400
        )
    except Exception as exc:  # engine/network failure
        raise HTTPException(status_code=502, detail=f"Vision model error: {exc}")
    answer = str((result or {}).get("content", "")).strip()
    if not answer:
        answer = "I couldn't make that out. Try holding the camera steady and closer."
    return VisionResponse(answer=answer, model=model)
