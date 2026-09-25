"""
CLI tool to bulk-enroll face embeddings from ordinary photos (not a live camera
scan), for testing whether recognition holds up against images the robot never
formally captured.

Prompts for a folder or a single image file. For a folder, every image inside
is treated as a photo of the same person, named after the folder; each photo
that yields a usable face gets enrolled as its own row under that name, so
matching later can draw on all of them.

Run from the repo root so VISION_FACE_STORE_PATH resolves to the same
faces.db the live vision-controller service uses:
    python robot_services/vision/detection/scan_folder_faces.py
"""

from pathlib import Path

import cv2

from config import (
    FACE_CONF_THRESHOLD,
    FACE_CROP_HEIGHT_RATIO,
    FACE_MIN_HEIGHT,
    FACE_MIN_SIZE,
    FACE_MIN_WIDTH,
    VISION_FACE_STORE_PATH,
)
from face_detector import FaceDetector
from face_embedder import FaceEmbedder
from face_store import FaceStore

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _iter_image_files(path):
    if path.is_file():
        yield path
        return
    for entry in sorted(path.iterdir()):
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS:
            yield entry


def _detect_best_face(face_detector, frame):
    frame_h, frame_w = frame.shape[:2]
    faces = face_detector.detect_faces_full_height(frame, (0, 0, frame_w, frame_h))
    if not faces:
        return None
    return max(faces, key=lambda face: face["width"] * face["height"])


def main():
    store_path = Path(VISION_FACE_STORE_PATH).resolve()
    print(f"Face store: {store_path} {'(exists)' if store_path.exists() else '(will be created)'}")
    print("This is resolved relative to the current working directory -- run this script")
    print("from the repo root, or the vision-controller service's working_dir, so it")
    print("matches the database the live detector process actually reads.")
    if input("Continue with this face store? [Y/n]: ").strip().lower() == "n":
        print("Aborted. cd to the correct directory (or set VISION_FACE_STORE_PATH) and rerun.")
        return

    raw_path = input("Folder or image file to scan: ").strip().strip('"')
    path = Path(raw_path).expanduser()
    if not path.exists():
        print(f"Path does not exist: {path}")
        return

    default_name = path.name if path.is_dir() else path.stem
    name = input(f"Enroll these faces under name [{default_name}]: ").strip() or default_name

    image_files = list(_iter_image_files(path))
    if not image_files:
        print(f"No image files found at {path}")
        return

    print(f"Found {len(image_files)} image(s). Loading face detector and embedder...")
    face_detector = FaceDetector(
        min_face_size=FACE_MIN_SIZE,
        min_face_width=FACE_MIN_WIDTH,
        min_face_height=FACE_MIN_HEIGHT,
        min_face_confidence=FACE_CONF_THRESHOLD,
        crop_height_ratio=FACE_CROP_HEIGHT_RATIO,
    )
    face_embedder = FaceEmbedder()
    face_store = FaceStore(VISION_FACE_STORE_PATH)

    enrolled = 0
    skipped = 0
    for image_path in image_files:
        frame = cv2.imread(str(image_path))
        if frame is None:
            print(f"  [skip] could not read image: {image_path.name}")
            skipped += 1
            continue

        face = _detect_best_face(face_detector, frame)
        if face is None:
            print(f"  [skip] no face detected: {image_path.name}")
            skipped += 1
            continue

        embedding = face_embedder.embed(frame, face["bbox"])
        if embedding is None:
            print(f"  [skip] could not compute embedding: {image_path.name}")
            skipped += 1
            continue

        face_id = face_store.enroll(name, embedding_close=embedding)
        print(f"  [ok] {image_path.name} -> face_id={face_id} (confidence={face['confidence']:.2f})")
        enrolled += 1

    print(f"\nEnrolled {enrolled} face(s) for '{name}', skipped {skipped}.")
    print(f"Face store: {store_path}")


if __name__ == "__main__":
    main()
