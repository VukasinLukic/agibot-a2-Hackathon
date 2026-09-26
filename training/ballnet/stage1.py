"""Train per-fold nets and cache candidates with held-out probabilities."""
import json, numpy as np, sys, torch, pickle
from evaluate import run_pipeline
from net import train
F = np.load('frames960.npy', mmap_mode='r')
d = np.load(sys.argv[1]); X, Y, meta = d['X'], d['Y'], d['meta']; tag = sys.argv[2]
epochs = int(sys.argv[3]) if len(sys.argv) > 3 else 20
split = 461
for name, trm in [('A', meta[:, 0] < split), ('B', meta[:, 0] >= split)]:
    net = train(X[trm], Y[trm], epochs=epochs)
    torch.save(net.state_dict(), f'net_{tag}_{name}.pt')
    out, cl, tm = run_pipeline(net, F)
    pickle.dump(cl, open(f'cl_{tag}_{name}.pkl', 'wb'))
    print(name, 'ms det/net %.1f/%.1f' % tm, flush=True)
