"""Tracker metrics on cached fold logs. Held-out halves only."""
import json, math, pickle, sys
import mht2
split, N = 461, 922
def metrics(out, gtd, sel):
    gt = {int(k): v for k, v in gtd['gt'].items()}; ign = {int(k): v for k, v in gtd['ignore'].items()}
    negs = {}
    for t, x, y in gtd.get('neg', []): negs.setdefault(int(t), []).append((x, y))
    tp = wrong = miss = fp = unv = 0; nb = ng = 0
    unv_list = []
    for t in sel:
        r = out.get(t); g = gt.get(t)
        obs = r is not None and r[0] == 'observed'
        if g is not None:
            ng += 1; tol = max(20.0, 0.6 * max(g[2], g[3]))
            if obs and math.hypot(r[1] - g[0], r[2] - g[1]) <= tol: tp += 1
            elif obs and any(math.hypot(r[1] - x, r[2] - y) <= 30 for x, y in ign.get(t, [])): pass
            elif obs: wrong += 1
            else: miss += 1
        else:
            nb += 1
            if not obs: continue
            if any(math.hypot(r[1] - x, r[2] - y) <= 30 for x, y in ign.get(t, [])): continue
            if any(math.hypot(r[1] - x, r[2] - y) <= 15 for x, y in negs.get(t, [])): fp += 1
            else: unv += 1; unv_list.append((t, r[1], r[2]))
    return dict(recall=round(tp / ng, 3), wrong=wrong, miss=miss, fp=fp, unverified=unv, gt=ng, nogt=nb), unv_list

def run(cl, kw):
    tr = mht2.Tracker(**kw); out = {}
    for t in range(N):
        cands, probs = cl[t]; out[t] = tr.step(t, cands, probs)
    return out

def evaluate(tag, gtd, kw, verbose=True):
    res = {}; allu = []
    for name, sel in (('A', range(split, N)), ('B', range(0, split))):
        cl = pickle.load(open(f'cl_{tag}_{name}.pkl', 'rb'))
        m, u = metrics(run(cl, kw), gtd, sel); res[name] = m; allu += u
    if verbose: print(kw, res)
    return res, allu

if __name__ == '__main__':
    gtd = json.load(open(sys.argv[2]))
    kw = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    res, allu = evaluate(sys.argv[1], gtd, kw)
    json.dump({'unver': allu}, open(f'unv_{sys.argv[1]}.json', 'w'))
