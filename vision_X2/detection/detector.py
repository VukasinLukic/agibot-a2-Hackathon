import torch
from ultralytics import YOLO


class PersonDetector:
    def __init__(
        self,
        model_name,
        person_class_id=0,
        conf_threshold=0.25,
        device=None,
        image_size=640,
    ):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = YOLO(model_name)
        self.model.to(self.device)
        self.person_class_id = person_class_id
        self.conf_threshold = conf_threshold
        self.image_size = image_size

    def detect_and_track(self, frame):
        with torch.inference_mode():
            results = self.model.track(
                frame,
                conf=self.conf_threshold,
                classes=[self.person_class_id],
                persist=True,
                tracker="bytetrack.yaml",
                verbose=False,
                device=self.device,
                half=self.device.startswith("cuda"),
                imgsz=self.image_size,
            )

        persons = []

        if results[0].boxes is None:
            return persons

        for box in results[0].boxes:
            if box.id is None:
                continue

            track_id = int(box.id[0])
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])

            persons.append({
                "track_id": track_id,
                "confidence": conf,
                "bbox": (x1, y1, x2, y2),
            })

        return persons
