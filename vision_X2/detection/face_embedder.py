import cv2
import numpy as np
import torch
from facenet_pytorch import InceptionResnetV1

EMBED_SIZE = 160


class FaceEmbedder:
    """Produces L2-normalized 512-d face embeddings via facenet-pytorch."""

    def __init__(self, device=None):
        self.device = torch.device(
            device if device is not None else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        self.model = InceptionResnetV1(pretrained="vggface2").eval().to(self.device)

    def embed(self, frame, face_bbox):
        crop = self._crop_face(frame, face_bbox)
        if crop is None:
            return None

        tensor = self._preprocess(crop)
        with torch.no_grad():
            embedding = self.model(tensor)[0].cpu().numpy()

        norm = np.linalg.norm(embedding)
        if norm <= 0.0:
            return None
        return (embedding / norm).astype(np.float32)

    def _crop_face(self, frame, face_bbox):
        x1, y1, x2, y2 = face_bbox
        frame_h, frame_w = frame.shape[:2]
        x1 = max(0, int(x1))
        y1 = max(0, int(y1))
        x2 = min(frame_w, int(x2))
        y2 = min(frame_h, int(y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    def _preprocess(self, crop):
        rgb_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb_crop, (EMBED_SIZE, EMBED_SIZE), interpolation=cv2.INTER_AREA)
        normalized = (resized.astype(np.float32) - 127.5) / 128.0
        tensor = torch.from_numpy(normalized).permute(2, 0, 1).unsqueeze(0)
        return tensor.to(self.device)
