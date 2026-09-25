import json
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
        existing_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(faces)")}
        migrations = {
            "canonical_name": "TEXT",
            "aliases": "TEXT NOT NULL DEFAULT '[]'",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "match_images": "TEXT NOT NULL DEFAULT 'both'",
            "image_close": "BLOB",
            "image_far": "BLOB",
        }
        for column, definition in migrations.items():
            if column not in existing_columns:
                self._conn.execute(f"ALTER TABLE faces ADD COLUMN {column} {definition}")
        self._conn.commit()

    def enroll_or_get(
        self,
        name,
        embedding_close=None,
        embedding_far=None,
        image_close=None,
        image_far=None,
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
            "INSERT INTO faces (face_id, name, canonical_name, embedding_close, embedding_far, "
            "image_close, image_far, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                face_id,
                name,
                name,
                _to_blob(embedding_close) if embedding_close is not None else None,
                _to_blob(embedding_far) if embedding_far is not None else None,
                image_close,
                image_far,
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
        cursor = self._conn.execute(
            "SELECT face_id, name, canonical_name, aliases, embedding_close, embedding_far, match_images FROM faces"
        )
        for face_id, name, canonical_name, aliases, embedding_close, embedding_far, match_images in cursor:
            selected_embeddings = []
            if match_images in ("both", "close"):
                selected_embeddings.append(_from_blob(embedding_close))
            if match_images in ("both", "far"):
                selected_embeddings.append(_from_blob(embedding_far))
            scores = [
                _cosine_similarity(query, stored)
                for stored in selected_embeddings
                if stored is not None
            ]
            if not scores:
                continue
            score = max(scores)
            if best is None or score > best["score"]:
                try:
                    parsed_aliases = json.loads(aliases or "[]")
                except (TypeError, ValueError):
                    parsed_aliases = []
                best = {
                    "face_id": face_id,
                    "name": name,
                    "display_name": name,
                    "canonical_name": canonical_name or name,
                    "aliases": parsed_aliases,
                    "score": score,
                }

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
        cursor = self._conn.execute(
            "SELECT face_id, name, canonical_name, aliases, notes, match_images, created_at, "
            "embedding_close IS NOT NULL, embedding_far IS NOT NULL, image_close IS NOT NULL, image_far IS NOT NULL "
            "FROM faces ORDER BY created_at"
        )
        return [
            {
                "face_id": face_id, "name": name, "display_name": name,
                "canonical_name": canonical_name or name,
                "aliases": json.loads(aliases or "[]"), "notes": notes or "",
                "match_images": match_images or "both", "created_at": created_at,
                "has_close_embedding": bool(has_close_embedding),
                "has_far_embedding": bool(has_far_embedding),
                "has_close_image": bool(has_close_image), "has_far_image": bool(has_far_image),
            }
            for (face_id, name, canonical_name, aliases, notes, match_images, created_at,
                 has_close_embedding, has_far_embedding, has_close_image, has_far_image) in cursor
        ]

    def update_identity(self, face_id, *, display_name, canonical_name, aliases, notes, match_images):
        if match_images not in {"both", "close", "far"}:
            raise ValueError("match_images must be both, close, or far")
        cursor = self._conn.execute(
            "UPDATE faces SET name = ?, canonical_name = ?, aliases = ?, notes = ?, match_images = ? WHERE face_id = ?",
            (display_name, canonical_name, json.dumps(list(aliases), ensure_ascii=False), notes, match_images, face_id),
        )
        self._conn.commit()
        if cursor.rowcount <= 0:
            raise LookupError("Face identity not found")
        return next(item for item in self.list_all() if item["face_id"] == face_id)

    def get_image(self, face_id, distance):
        if distance not in {"close", "far"}:
            raise ValueError("distance must be close or far")
        row = self._conn.execute(f"SELECT image_{distance} FROM faces WHERE face_id = ?", (face_id,)).fetchone()
        if row is None:
            raise LookupError("Face identity not found")
        return row[0]

    def close(self):
        self._conn.close()
