"""Fold a reviewed list of (t, x, y) into the candidate labels and the per-frame GT store."""
import json, math, sys, importlib
ds_in, rev_json, labmod, ds_out, gt_in, gt_out = sys.argv[1:7]
L = importlib.import_module(labmod)
ds = json.load(open(ds_in)); U = json.load(open(rev_json)); C = json.load(open('cands_v1.json'))
gtd = json.load(open(gt_in)); gtd.setdefault('neg', [])
lab = {(t, ci): v for t, ci, v in ds}
for k, (t, x, y) in enumerate(U):
    cs = C[str(t)]
    ci = min(range(len(cs)), key=lambda i: math.hypot(cs[i]['x'] - x, cs[i]['y'] - y))
    near = math.hypot(cs[ci]['x'] - x, cs[ci]['y'] - y) < 3
    v = 1 if k in L.POS else (-1 if k in L.SKIP else 0)
    if near: lab[(t, ci)] = v
    if v == 1:
        c = cs[ci]
        if str(t) not in gtd['gt']: gtd['gt'][str(t)] = [c['x'], c['y'], c['bw'], c['bh']]
    elif v == 0:
        gtd['neg'].append([t, x, y])
    else:
        gtd['ignore'].setdefault(str(t), []).append([x, y])
out = [[t, ci, v] for (t, ci), v in sorted(lab.items()) if v >= 0]
json.dump(out, open(ds_out, 'w')); json.dump(gtd, open(gt_out, 'w'))
print('pos', sum(o[2] for o in out), 'neg', sum(1 for o in out if o[2] == 0), 'gt frames', len(gtd['gt']), 'verified negs', len(gtd['neg']))
