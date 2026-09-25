"""Operator-facing metadata access for the local biometric face store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from robot_services.vision.detection.face_store import FaceStore


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FACE_STORE_PATH = REPO_ROOT / "robot_supervisor_v2" / "data" / "faces.db"


class FaceIdentityController:
    def __init__(self, service_manager=None):
        self.service_manager = service_manager

    def store_path(self) -> Path:
        configured = None
        if self.service_manager:
            service = self.service_manager.get("vision-controller")
            if service:
                configured = service.get_config().get("face_store_path")
        path = Path(str(configured)) if configured else DEFAULT_FACE_STORE_PATH
        return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()

    def _with_store(self, operation):
        store = FaceStore(self.store_path())
        try:
            return operation(store)
        finally:
            store.close()

    def list_faces(self) -> dict[str, Any]:
        return {
            "store_path": str(self.store_path()),
            "items": self._with_store(lambda store: store.list_all()),
        }

    def update_face(self, face_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        display_name = str(payload.get("display_name") or "").strip()
        canonical_name = str(payload.get("canonical_name") or display_name).strip()
        if not display_name or not canonical_name:
            raise ValueError("display_name and canonical_name are required")
        aliases = payload.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [part.strip() for part in aliases.split(",") if part.strip()]
        return self._with_store(
            lambda store: store.update_identity(
                face_id,
                display_name=display_name,
                canonical_name=canonical_name,
                aliases=aliases,
                notes=str(payload.get("notes") or "").strip(),
                match_images=str(payload.get("match_images") or "both"),
            )
        )

    def delete_face(self, face_id: str) -> bool:
        return self._with_store(lambda store: store.delete(face_id=face_id))

    def get_image(self, face_id: str, distance: str) -> bytes | None:
        return self._with_store(lambda store: store.get_image(face_id, distance))
