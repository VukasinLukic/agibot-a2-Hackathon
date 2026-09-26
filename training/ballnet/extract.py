import numpy as np, json, sys
import cand
if len(sys.argv) > 1: cand.SIDE_K = float(sys.argv[1])
from cand import CandidateSource, extract_patches
F = np.load('frames960.npy', mmap_mode='r')
ds = json.load(open('ds_v4.json'))
C = json.load(open('cands_v1.json'))
need = {}
for t, ci, v in ds: need.setdefault(t, []).append((ci, v))
src = CandidateSource(960, 540)
X, Y, meta = [], [], []
for t in range(len(F)):
    cands, _ = src.step(F[t])
    if t not in need: continue
    assert len(cands) == len(C[str(t)])
    sel = [ci for ci, _ in need[t]]
    X.append(extract_patches(src.last_bgr, src.last_diff, [cands[ci] for ci in sel]))
    for ci, v in need[t]:
        c = cands[ci]; Y.append(v); meta.append([t, ci, c['bw'], c['bh'], c['area'], c['fill'], c['sat'], c['val'], c['contrast']])
X = np.concatenate(X); Y = np.array(Y, np.int64); meta = np.array(meta, np.float32)
np.savez_compressed(sys.argv[2] if len(sys.argv) > 2 else 'ds_v4.npz', X=(X * 255).astype(np.uint8), Y=Y, meta=meta)
print(X.shape, Y.sum())
