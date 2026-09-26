"""Dump candidates, patches and current BallNet probs for every frame of a recording.

Uses the repo's CandidateSource and the same search ROI as live (calibrated table
plus the band above it), so training patches are exactly what the robot sees.

    python dump_cands.py <video> <table.json> <out.npz> [weights.onnx]
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from table_tennis.vision.ballnet import load_ballnet
from table_tennis.vision.calibration import read_calibration
from table_tennis.vision.candidates import CandidateParams, CandidateSource
from table_tennis.vision.track import _search_roi

video, table, out = sys.argv[1], sys.argv[2], sys.argv[3]
weights = sys.argv[4] if len(sys.argv) > 4 else str(Path(__file__).resolve().parents[2] / "models" / "ballnet" / "ballnet.onnx")
cal = read_calibration(table)
net = load_ballnet(weights)
cap = cv2.VideoCapture(video)
W, H = int(cap.get(3)), int(cap.get(4))
work_w = 960
work_h = round(H * work_w / W)
sx, sy = W / work_w, H / work_h
roi = _search_roi(None, cal)
work_roi = (int(roi.x / sx), int(roi.y / sy), int(np.ceil((roi.x + roi.width) / sx)), int(np.ceil((roi.y + roi.height) / sy)))
src = CandidateSource(work_w, work_h, CandidateParams(compensate=True), work_roi)
rows, patches, frames960 = [], [], []
t0 = time.time()
i = 0
while True:
    ok, img = cap.read()
    if not ok:
        break
    small = cv2.resize(img, (work_w, work_h), interpolation=cv2.INTER_AREA)
    cands = src.step(small)
    if cands:
        p = src.patches(cands)
        prob = net.probs(p)
        for c, pr in zip(cands, prob):
            rows.append([i, c.x, c.y, c.bw, c.bh, c.area, c.fill, c.sat, c.val, c.contrast, c.cmax, float(pr)])
        patches.append((p * 255).round().astype(np.uint8))
    frames960.append(cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 90])[1])
    i += 1
cols = "frame x y bw bh area fill sat val contrast cmax prob".split()
np.savez(out, rows=np.array(rows, np.float32), patches=np.concatenate(patches), cols=np.array(cols),
         jpgs=np.array(frames960, dtype=object), roi=np.array(work_roi))
print(f"frames {i}, candidates {len(rows)}, {time.time() - t0:.0f}s, roi {work_roi}")
