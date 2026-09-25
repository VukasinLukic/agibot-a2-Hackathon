import sqlite3
import time
import uuid
from pathlib import Path

import numpy as np


def _to_blob(embedding):
    return np.asarray(embedding, dtype=np.float32).tobytes()


def _from_blob(blob):
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def _cosine_similarity(a, b):
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


class FaceStore:
    """Local SQLite store of name<->face-embedding pairs."""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faces (
                face_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                embedding_close BLOB,
                embedding_far BLOB,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def enroll_or_get(
        self,
        name,
        embedding_close=None,
        embedding_far=None,
        *,
        dedupe_threshold=0.75,
    ):
        if embedding_close is None and embedding_far is None:
            raise ValueError("At least one embedding is required to enroll a face")

        existing = None
        for embedding in (embedding_close, embedding_far):
            if embedding is None:
                continue
            candidate = self.match(embedding, dedupe_threshold)
            if candidate is not None and (
                existing is None or candidate["score"] > existing["score"]
            ):
                existing = candidate
        if existing is not None:
            return {
                "face_id": existing["face_id"],
                "name": existing["name"],
                "created": False,
                "score": existing["score"],
            }

        face_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO faces (face_id, name, embedding_close, embedding_far, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                face_id,
                name,
                _to_blob(embedding_close) if embedding_close is not None else None,
                _to_blob(embedding_far) if embedding_far is not None else None,
                time.time(),
            ),
        )
        self._conn.commit()
        return {
            "face_id": face_id,
            "name": name,
            "created": True,
            "score": None,
        }

    def enroll(self, name, embedding_close=None, embedding_far=None):
        """Backward-compatible wrapper returning only the face id."""

        return self.enroll_or_get(
            name,
            embedding_close,
            embedding_far,
        )["face_id"]

    def match(self, embedding, threshold):
        query = np.asarray(embedding, dtype=np.float32)
        best = None
        cursor = self._conn.execute("SELECT face_id, name, embedding_close, embedding_far FROM faces")
        for face_id, name, embedding_close, embedding_far in cursor:
            scores = [
                _cosine_similarity(query, stored)
                for stored in (_from_blob(embedding_close), _from_blob(embedding_far))
                if stored is not None
            ]
            if not scores:
                continue
            score = max(scores)
            if best is None or score > best["score"]:
                best = {"face_id": face_id, "name": name, "score": score}

        if best is None or best["score"] < threshold:
            return None
        return best

    def delete(self, face_id=None, name=None):
        if not face_id and not name:
            raise ValueError("Provide face_id or name to delete a face")

        if face_id:
            cursor = self._conn.execute("DELETE FROM faces WHERE face_id = ?", (face_id,))
        else:
            cursor = self._conn.execute("DELETE FROM faces WHERE name = ?", (name,))
        self._conn.commit()
        return cursor.rowcount > 0

    def list_all(self):
        cursor = self._conn.execute("SELECT face_id, name, created_at FROM faces ORDER BY created_at")
        return [
            {"face_id": face_id, "name": name, "created_at": created_at}
            for face_id, name, created_at in cursor
        ]
