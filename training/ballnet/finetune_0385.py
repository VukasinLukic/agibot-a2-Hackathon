"""Fine-tune BallNet on IMG_0385 (the demo table) from reviewed ball tracklets.

1. Reviewed ball tracklets are extended frame by frame with a constant-velocity
   prediction, which recovers ball candidates the old net scored low.
2. Positives: those candidates. Negatives: every other candidate farther than
   NEAR px from a positive in the same frame; uncertain ones (old prob > 0.5
   and not in a reviewed tracklet) are left out.
3. Fine-tune from net_final.pt. With --split the last 40% of frames are held
   out and old vs new are compared there; without it all frames train.

    python finetune_0385.py --split        # measure
    python finetune_0385.py --out ballnet_0385   # final: writes .pt/.onnx/.npz
"""
import argparse
import json

import numpy as np
import torch
import torch.nn as nn

from net import BallNet, augment

BALL = [21, 27, 73, 96, 111, 113, 119, 152, 191, 208, 219, 272, 287, 288, 297, 307, 347, 397, 566, 679, 709, 729, 737, 1014]
REVIEWED_NOT_BALL = [
    int(x) for x in open("review_ids.txt").read().split() if int(x) not in set(BALL)
]
NEAR = 12.0
R1, R2 = 9.0, 14.0  # search radius one / two frames ahead, px at 960


def extend(rows, by_frame, chain):
    """Grow a tracklet both ways while a candidate sits where constant velocity puts it."""
    xy = rows[:, 1:3]
    frame = rows[:, 0].astype(int)
    out = list(chain)
    for direction in (1, -1):
        seq = out if direction == 1 else out[::-1]
        while len(seq) >= 2:
            a, b = seq[-2], seq[-1]
            va = (xy[b] - xy[a]) / max(1, abs(frame[b] - frame[a]))
            found = None
            for step, radius in ((1, R1), (2, R2)):
                t = frame[b] + direction * step
                if t < 0 or t >= len(by_frame) or not len(by_frame[t]):
                    continue
                pred = xy[b] + va * step
                cand = by_frame[t]
                dist = np.linalg.norm(xy[cand] - pred, axis=1)
                k = int(np.argmin(dist))
                if dist[k] <= radius:
                    found = int(cand[k])
                    break
            if found is None or found in seq:
                break
            seq.append(found)
        out = seq if direction == 1 else seq[::-1]
    return out


def fine_tune(X, Y, epochs, seed=0, lr=6e-4):
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    net = BallNet()
    net.load_state_dict(torch.load("net_final.pt", map_location="cpu"))
    opt = torch.optim.Adam(net.parameters(), lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    lossf = nn.BCEWithLogitsLoss()
    Xt = torch.from_numpy(X)
    Yt = torch.from_numpy(Y.astype(np.float32))
    pos, neg = np.flatnonzero(Y == 1), np.flatnonzero(Y == 0)
    for ep in range(epochs):
        net.train()
        idx = np.concatenate([neg, rng.choice(pos, len(neg) // 3)])
        rng.shuffle(idx)
        tot = 0.0
        for s in range(0, len(idx), 256):
            bi = idx[s:s + 256]
            xb = augment(Xt[bi].float() / 255.0, rng)
            loss = lossf(net(xb), Yt[bi])
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item() * len(bi)
        sched.step()
        print(f"  epoha {ep + 1}/{epochs} loss {tot / len(idx):.4f}", flush=True)
    net.eval()
    return net


def probs(net, X):
    out = []
    with torch.no_grad():
        for s in range(0, len(X), 2048):
            out.append(torch.sigmoid(net(torch.from_numpy(X[s:s + 2048]).float() / 255.0)).numpy())
    return np.concatenate(out)


def report(name, y, p):
    ps = np.sort(p[y == 1])
    line = [f"{name}: pozitivnih {int(y.sum())}"]
    for th in (0.5, 0.7):
        tp = int(((p >= th) & (y == 1)).sum())
        fp = int(((p >= th) & (y == 0)).sum())
        line.append(f"prag {th}: nadjeno {tp / max(1, y.sum()):.0%} loptica, laznih {fp}")
    print(" | ".join(line))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", action="store_true")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--out", default="ballnet_0385")
    args = ap.parse_args()

    d = np.load("c0385.npz", allow_pickle=True)
    rows, patches = d["rows"], d["patches"]
    T = json.load(open("t0385.json"))
    frame = rows[:, 0].astype(int)
    by_frame = [np.flatnonzero(frame == t) for t in range(int(frame.max()) + 1)]

    pos_rows = set()
    for tid in BALL:
        grown = extend(rows, by_frame, T[tid])
        pos_rows.update(grown)
    print(f"putanja loptice {len(BALL)}: pre produzenja {sum(len(T[t]) for t in BALL)}, posle {len(pos_rows)} kandidata")
    bad_rows = {i for tid in REVIEWED_NOT_BALL for i in T[tid]} - pos_rows

    y = np.full(len(rows), -1, np.int64)
    pos = np.array(sorted(pos_rows))
    y[pos] = 1
    pos_by_frame = {}
    for i in pos:
        pos_by_frame.setdefault(frame[i], []).append(rows[i, 1:3])
    for i in range(len(rows)):
        if y[i] == 1:
            continue
        near = pos_by_frame.get(frame[i])
        if near is not None and np.min(np.linalg.norm(np.array(near) - rows[i, 1:3], axis=1)) < NEAR:
            continue
        if rows[i, 11] > 0.5 and i not in bad_rows:
            continue  # the old net thinks ball and nobody checked: leave it out
        y[i] = 0
    use = y >= 0
    X = patches.transpose(0, 3, 1, 2)
    print(f"skup: pozitivnih {int((y == 1).sum())}, negativnih {int((y == 0).sum())}, izostavljeno {int((~use).sum())}")

    if args.split:
        cut = int(0.6 * len(by_frame))
        tr = use & (frame < cut)
        te = use & (frame >= cut)
        net = fine_tune(X[tr], y[tr], args.epochs)
        report("STARI model (test deo)", y[te], rows[te, 11])
        report("NOVI model  (test deo)", y[te], probs(net, X[te]))
        torch.save(net.state_dict(), args.out + "_split.pt")
        export(net, args.out + "_split")
    else:
        net = fine_tune(X[use], y[use], args.epochs)
        torch.save(net.state_dict(), args.out + ".pt")
        export(net, args.out)


def export(net, base):
    net.eval()
    torch.onnx.export(net, torch.zeros(1, 4, 32, 32), base + ".onnx", input_names=["x"], output_names=["logit"],
                      dynamic_axes={"x": {0: "n"}, "logit": {0: "n"}}, opset_version=13, dynamo=False)
    m = {"f.0": "c0", "f.3": "c1", "f.6": "c2", "h.1": "l0", "h.3": "l1"}
    out = {}
    for k, v in net.state_dict().items():
        base_key, kind = k.rsplit(".", 1)
        out[m[base_key] + ("w" if kind == "weight" else "b")] = v.numpy().astype(np.float32)
    np.savez(base + ".npz", **out)
    print("izvezeno", base + ".onnx", base + ".npz")


if __name__ == "__main__":
    main()
