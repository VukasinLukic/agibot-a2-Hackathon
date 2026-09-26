"""Online pipeline on the cached video; metrics on a held-out time half."""
import json, math, numpy as np, time, sys, torch
from cand import CandidateSource, extract_patches
from net import BallNet, predict
import mht2

def run_pipeline(net, frames, tracker_kw=None, src_kw=None, rng=None):
    src = CandidateSource(960, 540, **(src_kw or {}))
    tr = mht2.Tracker(**(tracker_kw or {}))
    out, cand_log = {}, {}
    t_det = t_net = 0.0
    for t in range(len(frames) if rng is None else rng.stop):
        a = time.perf_counter()
        cands, _ = src.step(frames[t])
        b = time.perf_counter()
        probs = predict(net, (extract_patches(src.last_bgr, src.last_diff, cands) * 255).astype(np.uint8)) if cands else []
        c = time.perf_counter()
        t_det += b - a; t_net += c - b
        cand_log[t] = (cands, list(map(float, probs)))
        out[t] = tr.step(t, cands, list(map(float, probs)))
    n = len(out)
    return out, cand_log, (1000 * t_det / n, 1000 * t_net / n)

def score(out, gtd, frames_sel, cand_log=None):
    gt = {int(k): v for k, v in gtd['gt'].items()}; ign = {int(k): v for k, v in gtd['ignore'].items()}
    tp = wrong = miss = unver = pred_ok = 0; cand_rec = 0; ngt = 0
    unver_list = []
    for t in frames_sel:
        r = out.get(t); g = gt.get(t)
        obs = r is not None and r[0] == 'observed'
        if g is not None:
            ngt += 1
            tol = max(20.0, 0.6 * max(g[2], g[3]))
            if cand_log is not None:
                if any(math.hypot(c['x'] - g[0], c['y'] - g[1]) <= tol for c in cand_log[t][0]): cand_rec += 1
            if obs and math.hypot(r[1] - g[0], r[2] - g[1]) <= tol: tp += 1
            elif obs and any(math.hypot(r[1] - x, r[2] - y) <= 45 for x, y in ign.get(t, [])): pass
            elif obs: wrong += 1
            else: miss += 1
        elif obs:
            unver += 1; unver_list.append((t, r[1], r[2]))
    return dict(gt_frames=ngt, recall=tp / max(ngt, 1), wrong=wrong, miss=miss, unverified=unver,
                precision_on_gt=tp / max(tp + wrong, 1), cand_recall=cand_rec / max(ngt, 1)), unver_list

if __name__ == '__main__':
    from net import train
    F = np.load('frames960.npy', mmap_mode='r')
    gtd = json.load(open(sys.argv[2]))
    d = np.load(sys.argv[1]); X, Y, meta = d['X'], d['Y'], d['meta']
    split = 461; res = {}
    for name, trm, sel in [('A', meta[:, 0] < split, range(split, len(F))), ('B', meta[:, 0] >= split, range(0, split))]:
        net = train(X[trm], Y[trm], epochs=20)
        torch.save(net.state_dict(), f'net_fold{name}.pt')
        out, cl, tm = run_pipeline(net, F)
        m, unv = score(out, gtd, sel, cl)
        json.dump({'out': out, 'unver': unv}, open(f'eval_fold{name}.json', 'w'))
        print(name, m, 'ms det/net %.1f/%.1f' % tm, flush=True)
