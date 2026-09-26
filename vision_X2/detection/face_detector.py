import cv2
import logging
import torch

log = logging.getLogger(__name__)
from facenet_pytorch import MTCNN
from PIL import Image


class FaceDetector:
    def __init__(
        self,
        device=None,
        min_face_size=40,
        min_face_width=80,
        min_face_height=80,
        min_face_confidence=0.90,
        crop_height_ratio=0.60,
    ):
        self.device = torch.device(device if device is not None else ("cuda" if torch.cuda.is_available() else "cpu"))
        
        if (self.device == "cpu"):
            print("Using CPU! Something is wrong with CUDA\nIt could be the CUDA version is wrong, or Torch is wrong! Check this please!")

        self.min_face_width = min_face_width
        self.min_face_height = min_face_height
        self.min_face_confidence = min_face_confidence
        self.crop_height_ratio = crop_height_ratio
        self.mtcnn = MTCNN(
            keep_all=True,
            device=self.device,
            min_face_size=min_face_size,
            thresholds=[0.6, 0.7, 0.7],
            post_process=False,
        )

    def confirm_person_face(self, frame, person):
        return bool(self.detect_person_faces(frame, person))

    def detect_person_faces(self, frame, person):
        x1, y1, x2, y2 = person["bbox"]
        frame_h, frame_w = frame.shape[:2]

        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(frame_w, x2)
        y2 = min(frame_h, y2)

        if x2 <= x1 or y2 <= y1:
            return []

        crop_bottom = y1 + int((y2 - y1) * self.crop_height_ratio)
        return self._detect_faces_in_crop(frame, x1, y1, x2, crop_bottom)

    def detect_faces_full_height(self, frame, bbox):
        x1, y1, x2, y2 = bbox
        frame_h, frame_w = frame.shape[:2]

        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(frame_w, x2)
        y2 = min(frame_h, y2)

        if x2 <= x1 or y2 <= y1:
            return []

        return self._detect_faces_in_crop(frame, x1, y1, x2, y2)

    def _detect_faces_in_crop(self, frame, x1, y1, x2, crop_bottom):
        person_crop = frame[y1:crop_bottom, x1:x2]
        if person_crop.size == 0:
            return []

        rgb_crop = cv2.cvtColor(person_crop, cv2.COLOR_BGR2RGB)
        pil_crop = Image.fromarray(rgb_crop)
        try:
            boxes, probs = self.mtcnn.detect(pil_crop)
        except RuntimeError:
            return []

        if boxes is None or probs is None:
            return []

        faces = []
        for box, prob in zip(boxes, probs):
            if prob is None or prob < self.min_face_confidence:
                continue

            fx1, fy1, fx2, fy2 = [int(value) for value in box]
            face_width = fx2 - fx1
            face_height = fy2 - fy1

            size_ok = face_width >= self.min_face_width and face_height >= self.min_face_height
            log.info(f"[face] w={face_width} h={face_height} conf={prob:.2f} | min_w={self.min_face_width} min_h={self.min_face_height} -> {'SIZE OK - will count toward lock' if size_ok else 'TOO SMALL - filtered out'}")
            if face_width < self.min_face_width or face_height < self.min_face_height:
                continue

            faces.append({
                "bbox": (x1 + fx1, y1 + fy1, x1 + fx2, y1 + fy2),
                "confidence": float(prob),
                "width": face_width,
                "height": face_height,
            })

        return faces
