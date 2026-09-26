"""Contact sheet of tracklets: crop of the 960 frame around the path, path drawn, id in the corner.

    python sheet_tracklets.py c0385.npz t0385.json out.png [ids...|random N|sorted key]
"""
import json
import sys

import cv2
import numpy as np

d = np.load(sys.argv[1], allow_pickle=True)
r, jpgs = d["rows"], d["jpgs"]
T = json.load(open(sys.argv[2]))
out = sys.argv[3]
ids = [int(x) for x in sys.argv[4:]] if len(sys.argv) > 4 else list(range(min(24, len(T))))
S = 120
tiles = []
for tid in ids:
    tr = T[tid]
    pts = r[tr][:, 1:3]
    mid = tr[len(tr) // 2]
    img = cv2.imdecode(jpgs[int(r[mid, 0])], cv2.IMREAD_COLOR)
    cx, cy = pts.mean(0)
    half = max(40, int(np.ptp(pts, 0).max() / 2) + 20)
    x0, y0 = int(max(0, cx - half)), int(max(0, cy - half))
    crop = img[y0:int(cy + half), x0:int(cx + half)].copy()
    for (x, y) in pts:
        cv2.circle(crop, (int(x - x0), int(y - y0)), 1, (0, 255, 255), -1)
    crop = cv2.resize(crop, (S, S), interpolation=cv2.INTER_NEAREST)
    cv2.putText(crop, str(tid), (2, 14), 0, 0.45, (0, 0, 255), 2)
    tiles.append(crop)
while len(tiles) % 8:
    tiles.append(np.zeros((S, S, 3), np.uint8))
cv2.imwrite(out, np.vstack([np.hstack(tiles[i:i + 8]) for i in range(0, len(tiles), 8)]))
