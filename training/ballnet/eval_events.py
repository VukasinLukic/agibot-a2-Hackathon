import json, pickle, sys, mht3, events_proto as E
gt = {int(k): (v[0], v[1]) for k, v in json.load(open('gt_v4.json'))['gt'].items()}
trk = {}
for name, sel in (('A', range(461, 922)), ('B', range(0, 461))):
    cl = pickle.load(open(f'cl_v3_{name}.pkl', 'rb')); tr = mht3.Tracker()
    for t in range(922):
        r = tr.step(t, *cl[t])
        if t in sel and r and r[0] == 'observed': trk[t] = (r[1], r[2])
kw = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
G = E.detect(gt, **kw); P = E.detect(trk, **kw)
for kind in ('bounce', 'hit'):
    g = [e for e in G if e[1] == kind]; p = [e for e in P if e[1] == kind]
    used = set(); tp = 0
    for e in p:
        m = [i for i, f in enumerate(g) if i not in used and abs(f[0] - e[0]) <= 2]
        if m: used.add(m[0]); tp += 1
    print(kind, 'gt', len(g), 'pred', len(p), 'tp', tp, 'missed', [f[0] for i, f in enumerate(g) if i not in used],
          'extra', [e[0] for e in p if not any(abs(f[0] - e[0]) <= 2 for f in g)])
