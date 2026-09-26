import numpy as np, sys, time
from net import train, predict
d = np.load(sys.argv[1]); X, Y, meta = d['X'], d['Y'], d['meta']
t = meta[:, 0]; split = 461
def auc(y, p):
    o = np.argsort(p); r = np.empty(len(p)); r[o] = np.arange(len(p))
    npos = y.sum(); nneg = len(y) - npos
    return (r[y == 1].sum() - npos * (npos - 1) / 2) / (npos * nneg)
for name, tr, te in [('A', t < split, t >= split), ('B', t >= split, t < split)]:
    t0 = time.time()
    net = train(X[tr], Y[tr], epochs=int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    p = predict(net, X[te]); y = Y[te]
    msg = [f'fold {name} train {tr.sum()} test {te.sum()} pos {y.sum()} auc {auc(y, p):.4f}']
    ps = np.sort(p[y == 1])
    for r in (0.95, 0.9, 0.8):
        th = ps[int((1 - r) * len(ps))]; msg.append(f'fp@rec{r}: {((p >= th) & (y == 0)).sum()}')
    for th in ():
        tp = ((p >= th) & (y == 1)).sum(); fp = ((p >= th) & (y == 0)).sum()
        msg.append(f'th{th}: rec {tp / y.sum():.3f} fp {fp}')
    print(' | '.join(msg), f'{time.time() - t0:.0f}s', flush=True)
