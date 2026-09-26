"""Per-frame GT from candidate labels. Distant duplicates: keep the one continuing the flight; others unknown."""
import json, math, sys
ds_path, out_path = sys.argv[1], sys.argv[2]
ds = json.load(open(ds_path)); C = json.load(open('cands_v1.json'))
pos = {}
for t, ci, v in ds:
    if v == 1: pos.setdefault(t, []).append(ci)
def box(cs):
    x0 = min(c['x'] - c['bw'] / 2 for c in cs); x1 = max(c['x'] + c['bw'] / 2 for c in cs)
    y0 = min(c['y'] - c['bh'] / 2 for c in cs); y1 = max(c['y'] + c['bh'] / 2 for c in cs)
    return [(x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0]
gt, ignore = {}, {}
for t, cis in pos.items():
    cs = [C[str(t)][ci] for ci in cis]
    if max(math.hypot(a['x'] - b['x'], a['y'] - b['y']) for a in cs for b in cs) <= 45:
        gt[t] = box(cs)
for t, cis in pos.items():
    if t in gt: continue
    ref = [gt[k] for k in (t - 1, t + 1, t - 2, t + 2) if k in gt]
    cs = [C[str(t)][ci] for ci in cis]
    if ref:
        best = min(cs, key=lambda c: min(math.hypot(c['x'] - r[0], c['y'] - r[1]) for r in ref))
    else:
        best = max(cs, key=lambda c: c['area'])
    gt[t] = box([c for c in cs if math.hypot(c['x'] - best['x'], c['y'] - best['y']) <= 45])
    ignore[t] = [[c['x'], c['y']] for c in cs if math.hypot(c['x'] - best['x'], c['y'] - best['y']) > 45]
json.dump({'gt': gt, 'ignore': ignore}, open(out_path, 'w'))
print('gt frames', len(gt), 'ignore', ignore)
