"""Pseudo-labels from physics: candidates that form smooth flight paths are the ball.

A candidate is kept when a neighbour one frame before and one after sit on a
near straight, constant-speed line (|a + c - 2b| small). Consecutive kept
candidates chain into tracklets. Output: tracklets as lists of row indices.

    python tracklets_auto.py c0385.npz t0385.json
"""
import json
import sys

import numpy as np

ACC_PX = 3.0        # |a + c - 2b| at 960 px width
MIN_SPEED = 2.0     # px / frame; slower is a hand or noise
MAX_SPEED = 60.0
MIN_LEN = 5

d = np.load(sys.argv[1], allow_pickle=True)
r = d["rows"]
frame = r[:, 0].astype(int)
xy = r[:, 1:3]
area = r[:, 5]
n_frames = int(frame.max()) + 1
by_frame = [np.flatnonzero(frame == t) for t in range(n_frames)]

nxt = {}  # row -> best next row, from accepted triplets
kept = set()
for t in range(1, n_frames - 1):
    A, B, C = by_frame[t - 1], by_frame[t], by_frame[t + 1]
    if not len(A) or not len(B) or not len(C):
        continue
    for b in B:
        pb = xy[b]
        da = np.linalg.norm(xy[A] - pb, axis=1)
        dc = np.linalg.norm(xy[C] - pb, axis=1)
        ia = A[(da >= MIN_SPEED) & (da <= MAX_SPEED)]
        ic = C[(dc >= MIN_SPEED) & (dc <= MAX_SPEED)]
        if not len(ia) or not len(ic):
            continue
        # every (a, c) pair: prediction error of c from a, b
        pred = 2 * pb - xy[ia]                     # where c should be for each a
        err = np.linalg.norm(pred[:, None, :] - xy[ic][None, :, :], axis=2)
        k = np.unravel_index(np.argmin(err), err.shape)
        if err[k] > ACC_PX:
            continue
        a, c = int(ia[k[0]]), int(ic[k[1]])
        ratio = area[[a, b, c]]
        if ratio.max() > 4 * max(ratio.min(), 1):
            continue
        kept.update((a, b, c))
        nxt.setdefault(a, b)
        nxt.setdefault(b, c)

# chain
prev = {v: k for k, v in nxt.items()}
tracklets = []
for start in sorted(kept):
    if start in prev and prev[start] in kept:
        continue
    chain = [start]
    while chain[-1] in nxt:
        chain.append(nxt[chain[-1]])
    if len(chain) >= MIN_LEN:
        tracklets.append([int(i) for i in chain])
json.dump(tracklets, open(sys.argv[2], "w"))
frames_with = {int(frame[i]) for t in tracklets for i in t}
print(f"putanja {len(tracklets)}, kandidata u njima {sum(map(len, tracklets))}, kadrova {len(frames_with)}")
lens = sorted(map(len, tracklets))
print("duzine p50", lens[len(lens) // 2] if lens else 0, "max", max(lens) if lens else 0)
