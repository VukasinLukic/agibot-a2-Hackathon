import torch, torch.nn as nn, numpy as np

class BallNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.f = nn.Sequential(
            nn.Conv2d(4, 12, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(12, 24, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(24, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.h = nn.Sequential(nn.Flatten(), nn.Linear(32 * 16, 32), nn.ReLU(), nn.Linear(32, 1))
    def forward(self, x):
        return self.h(self.f(x - 0.5)).squeeze(1)

def augment(x, rng):
    # x: B,4,P,P float in [0,1]
    b = x.shape[0]
    if rng.random() < 0.5: x = x.flip(3)
    if rng.random() < 0.5: x = x.flip(2)
    k = int(rng.integers(0, 4)); x = torch.rot90(x, k, (2, 3))
    gain = torch.from_numpy(rng.uniform(0.7, 1.3, (b, 1, 1, 1)).astype(np.float32))
    chan = torch.from_numpy(rng.uniform(0.9, 1.1, (b, 3, 1, 1)).astype(np.float32))
    bias = torch.from_numpy(rng.uniform(-0.1, 0.1, (b, 1, 1, 1)).astype(np.float32))
    rgb = (x[:, :3] * gain * chan + bias)
    d = (x[:, 3:] - 0.5) * torch.from_numpy(rng.uniform(0.6, 1.4, (b, 1, 1, 1)).astype(np.float32)) + 0.5
    x = torch.cat([rgb, d], 1)
    x = x + torch.randn_like(x) * 0.02
    sh = rng.integers(-2, 3, 2)
    x = torch.roll(x, (int(sh[0]), int(sh[1])), (2, 3))
    return x.clamp(0, 1)

def train(X, Y, epochs=25, seed=0, log=None):
    torch.manual_seed(seed); rng = np.random.default_rng(seed)
    net = BallNet(); opt = torch.optim.Adam(net.parameters(), 2e-3, weight_decay=1e-4)
    Xt = torch.from_numpy(X.astype(np.float32) / 255.0); Yt = torch.from_numpy(Y.astype(np.float32))
    pos = np.where(Y == 1)[0]; neg = np.where(Y == 0)[0]
    lossf = nn.BCEWithLogitsLoss()
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    for ep in range(epochs):
        net.train()
        # balanced epoch: all negatives, positives oversampled to 1/4 of the batch
        idx = np.concatenate([neg, rng.choice(pos, len(neg) // 3)]); rng.shuffle(idx)
        tot = 0.0
        for s in range(0, len(idx), 256):
            bi = idx[s:s + 256]
            xb = augment(Xt[bi], rng); yb = Yt[bi]
            loss = lossf(net(xb), yb)
            opt.zero_grad(); loss.backward(); opt.step(); tot += loss.item() * len(bi)
        sched.step()
        if log: log(ep, tot / len(idx))
    net.eval(); return net

def predict(net, X):
    with torch.no_grad():
        out = []
        for s in range(0, len(X), 1024):
            out.append(torch.sigmoid(net(torch.from_numpy(X[s:s + 1024].astype(np.float32) / 255.0))).numpy())
        return np.concatenate(out) if out else np.zeros(0)
