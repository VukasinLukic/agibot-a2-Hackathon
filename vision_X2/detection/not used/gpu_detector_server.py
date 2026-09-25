#!/usr/bin/env python3
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
import uvicorn

HOST = os.getenv("GPU_DETECTOR_HOST", "0.0.0.0")
PORT = int(os.getenv("GPU_DETECTOR_PORT", "8765"))

LATEST_FRAME: Optional[bytes] = None
LATEST_FRAME_WIDTH: Optional[int] = None
LATEST_FRAME_HEIGHT: Optional[int] = None

app = FastAPI()

LATEST_LOCK_STATE: Dict[str, Any] = {
    "event": "unlocked",
    "locked": False,
    "locked_track_id": None,
    "previous_locked_track_id": None,
    "timestamp": None,
    "raw_candidate_id": None,
    "confirmed_candidate_id": None,
    "persons": [],
}


class LockStateEvent(BaseModel):
    event: str
    locked: bool
    locked_track_id: Optional[int] = None
    previous_locked_track_id: Optional[int] = None
    timestamp: Optional[float] = None
    raw_candidate_id: Optional[int] = None
    confirmed_candidate_id: Optional[int] = None
    persons: List[Dict[str, Any]] = Field(default_factory=list)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}


@app.post("/lock-state")
def update_lock_state(event: LockStateEvent) -> Dict[str, Any]:
    global LATEST_LOCK_STATE
    LATEST_LOCK_STATE = event.dict()
    return {"ok": True, "lock_state": LATEST_LOCK_STATE}


@app.get("/lock-state")
def get_lock_state() -> Dict[str, Any]:
    return LATEST_LOCK_STATE


@app.post("/frame")
async def post_frame(file: UploadFile = File(...)) -> Dict[str, Any]:
    global LATEST_FRAME, LATEST_FRAME_WIDTH, LATEST_FRAME_HEIGHT
    data = await file.read()
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="failed to decode image")
    LATEST_FRAME = data
    LATEST_FRAME_HEIGHT, LATEST_FRAME_WIDTH = img.shape[:2]
    return {"ok": True, "width": LATEST_FRAME_WIDTH, "height": LATEST_FRAME_HEIGHT}


@app.get("/frame")
def get_frame() -> Response:
    if LATEST_FRAME is None:
        raise HTTPException(status_code=503, detail="no frame available yet")
    return Response(content=LATEST_FRAME, media_type="image/jpeg")


@app.get("/frame/info")
def get_frame_info() -> Dict[str, Any]:
    if LATEST_FRAME is None:
        raise HTTPException(status_code=503, detail="no frame available yet")
    return {"width": LATEST_FRAME_WIDTH, "height": LATEST_FRAME_HEIGHT}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
