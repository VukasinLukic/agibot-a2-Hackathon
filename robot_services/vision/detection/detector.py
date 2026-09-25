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
        self._precision_kwargs = self._resolve_precision_kwargs()

    def _resolve_precision_kwargs(self):
        """Pick the FP16 argument name this ultralytics version actually wants.

        ultralytics >= 8.4 replaced `half=True` with `quantize=16` and emits a
        DeprecationWarning on every single inference call when `half` is passed.
        At detector FPS that warning floods the log with tens of lines per second
        and buries the detection output. Resolve the right keyword once here.
        """
        use_fp16 = str(self.device).startswith("cuda")
        try:
            from ultralytics.utils import DEFAULT_CFG_DICT

            supports_quantize = "quantize" in DEFAULT_CFG_DICT
        except Exception:
            supports_quantize = False

        if supports_quantize:
            # 16 == FP16; omit entirely for FP32 (None would also be accepted).
            return {"quantize": 16} if use_fp16 else {}
        return {"half": use_fp16}

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
                imgsz=self.image_size,
                **self._precision_kwargs,
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
