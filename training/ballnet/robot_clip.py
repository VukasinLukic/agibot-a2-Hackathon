"""BallNet on a robot TTCLIP: auto labels, per-stage metrics, fine-tune.

Run from the repo root (the robot camera is fixed while ARM holds, so a median
background is a clean reference):

    python training/ballnet/robot_clip.py convert SCREEN.mp4 OUT.ttclip   (screen recording of the camera view)
    python training/ballnet/robot_clip.py labels CLIP.ttclip OUT.labels.json
    python training/ballnet/robot_clip.py eval   CLIP.ttclip OUT.labels.json [--weights W.onnx] [--from-frame N]
    python training/ballnet/robot_clip.py train  CLIP.ttclip OUT.labels.json --out models/ballnet/ballnet_robot [--test-from N]

labels: background-subtraction blobs linked into ballistic tracklets. Check the
written PNG before trusting them (players and the net are dropped as dense).
eval: is the ball among candidates, does BallNet score it > 0.5, does the
tracker confirm it. Same CandidateSource, patches and Tracker as the robot.
train: fine-tune models/ballnet/net_final.pt on candidates of this clip
(positive within POS_PX of a label, negative farther than NEG_PX), on GPU if
present. --test-from holds out frames >= N and compares old vs new there.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from table_tennis.vision.ballnet import load_ballnet  # noqa: E402
from table_tennis.vision.pipeline import BallNetPipeline  # noqa: E402

POS_PX, NEG_PX = 6.0, 15.0
HEAD = struct.Struct("<8sIIIQ")


def read_clip(path: str) -> np.ndarray:
    with open(path, "rb") as handle:
        magic, width, height, count, _period = HEAD.unpack(handle.read(HEAD.size))
        if magic != b"TTCLIP01":
            raise SystemExit(f"not a TTCLIP: {path}")
        data = np.frombuffer(handle.read(count * width * height * 3), np.uint8)
    return data.reshape(-1, height, width, 3)


# ---------------------------------------------------------------- labels


def blobs(frames: np.ndarray) -> list[list[tuple[float, float]]]:
    background = np.median(frames[::5], axis=0).astype(np.uint8)
    out: list[list[tuple[float, float]]] = [[]]
    for i in range(1, len(frames)):
        moving = (cv2.absdiff(frames[i], background).max(axis=2) > 25) & (cv2.absdiff(frames[i], frames[i - 1]).max(axis=2) > 15)
        count, labels, stats, centers = cv2.connectedComponentsWithStats(moving.astype(np.uint8), 8)
        big = np.isin(labels, [j for j in range(1, count) if stats[j, 4] > 300]).astype(np.uint8)
        big = cv2.dilate(big, np.ones((15, 15), np.uint8))
        found = [
            (float(centers[j][0]), float(centers[j][1]))
            for j in range(1, count)
            if 5 <= stats[j, 4] <= 90 and max(stats[j, 2], stats[j, 3]) <= 18 and not big[int(centers[j][1]), int(centers[j][0])]
        ]
        # A ball is alone; players' hands and the flapping net come in clusters.
        out.append([p for p in found if sum(abs(p[0] - q[0]) < 25 and abs(p[1] - q[1]) < 25 for q in found) <= 2])
    return out


def tracklets(points: list[list[tuple[float, float]]]) -> list[list[tuple[int, float, float]]]:
    done, active = [], []
    for i, here in enumerate(points):
        used, keep = set(), []
        for t in active:
            f1, x1, y1 = t[-1]
            vx, vy = ((x1 - t[-2][1]) / (f1 - t[-2][0]), (y1 - t[-2][2]) / (f1 - t[-2][0])) if len(t) > 1 else (0.0, 0.0)
            gap = i - f1
            px, py = x1 + vx * gap, y1 + vy * gap + 0.6 * gap * gap
            best, dist = None, (14.0 if len(t) > 1 else 30.0)
            for k, (x, y) in enumerate(here):
                d = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
                if k not in used and d < dist:
                    best, dist = k, d
            if best is not None:
                used.add(best)
                t.append((i, *here[best]))
                keep.append(t)
            elif gap < 3:
                keep.append(t)
            else:
                done.append(t)
        keep += [[(i, x, y)] for k, (x, y) in enumerate(here) if k not in used]
        active = keep
    done += active

    def ballistic(t):
        if len(t) < 6:
            return False
        xy = np.array([p[1:] for p in t])
        span = np.linalg.norm(xy.max(0) - xy.min(0))
        return span > 40 and np.median(np.linalg.norm(np.diff(xy, axis=0), axis=1)) > 2.5

    return [t for t in done if ballistic(t)]


def cmd_labels(args) -> None:
    frames = read_clip(args.clip)
    tracks = tracklets(blobs(frames))
    labels = {int(f): [round(x, 1), round(y, 1)] for t in tracks for f, x, y in t}
    Path(args.labels).write_text(json.dumps({"clip": Path(args.clip).name, "frames": len(frames), "ball": labels, "tracks": tracks}))
    view = np.median(frames[::5], axis=0).astype(np.uint8)
    for t in tracks:
        for a, b in zip(t, t[1:]):
            cv2.line(view, (int(a[1]), int(a[2])), (int(b[1]), int(b[2])), (0, 0, 255), 1)
        cv2.putText(view, str(t[0][0]), (int(t[0][1]), int(t[0][2])), 0, 0.35, (0, 255, 255), 1)
    cv2.imwrite(str(Path(args.labels).with_suffix(".png")), view)
    print(f"{len(tracks)} putanja, loptica u {len(labels)} od {len(frames)} kadrova -> {args.labels} (+ .png za pregled)")


# ---------------------------------------------------------------- chain


def run_chain(frames, weights, keep_patches=False, darker=False):
    """Same CandidateSource/patches/Tracker as the robot. Yields per frame (cands, probs, hit, patches, scale)."""
    from table_tennis.vision.candidates import CandidateParams

    pipe = BallNetPipeline(load_ballnet(weights), candidate_params=CandidateParams(compensate=False, darker=darker))
    source, tracker = pipe._ready(frames.shape[2], frames.shape[1])
    sx, sy = pipe._scale
    for i, image in enumerate(frames):
        work = image if pipe._work == (image.shape[1], image.shape[0]) else cv2.resize(image, pipe._work)
        cands = source.step(work)
        patches = source.patches(cands) if cands else None
        probs = [float(p) for p in pipe._net.probs(patches)] if patches is not None else []
        hit = tracker.step(i, cands, probs)
        yield i, [(c.x * sx, c.y * sy) for c in cands], probs, hit and (hit.x * sx, hit.y * sy), (patches if keep_patches else None)


def cmd_eval(args) -> None:
    frames = read_clip(args.clip)
    data = json.loads(Path(args.labels).read_text())
    ball = {int(k): v for k, v in data["ball"].items() if int(k) >= args.from_frame}
    track_of = {int(f): t[0][0] for t in data.get("tracks", []) for f, _x, _y in t}
    per_track: dict[int, list[int]] = {}
    among = scored = confirmed = false_hits = frames_seen = 0
    for i, cands, probs, hit, _ in run_chain(frames, args.weights, darker=args.darker):
        if i < args.from_frame:
            continue
        frames_seen += 1
        if i not in ball:
            false_hits += hit is not None and not any(abs(i - k) <= 2 for k in ball)
            continue
        d = [np.hypot(x - ball[i][0], y - ball[i][1]) for x, y in cands]
        row = per_track.setdefault(track_of.get(i, -1), [0, 0, 0])
        row[0] += 1
        if d and min(d) <= POS_PX:
            among += 1
            row[1] += 1
            scored += probs[int(np.argmin(d))] > 0.5
        ok = hit is not None and np.hypot(hit[0] - ball[i][0], hit[1] - ball[i][1]) <= 8
        confirmed += ok
        row[2] += ok
    n = max(1, len(ball))
    print(f"{Path(args.weights).name} od kadra {args.from_frame}: loptica u {len(ball)} kadrova")
    print(f"  medju kandidatima {among / n:.0%} | BallNet>0.5 {scored / n:.0%} | tracker potvrdio {confirmed / n:.0%}"
          f" | tracker bez loptice (lazno) {false_hits} od {frames_seen - len(ball)} kadrova")
    for start, (n_t, c_t, k_t) in sorted(per_track.items()):
        print(f"    putanja {start}: kadrova {n_t}, medju kandidatima {c_t}, tracker {k_t}")


# ---------------------------------------------------------------- train


def cached_dataset(clip: str, labels: str):
    """dataset() for one labeled clip, cached next to the labels until either file changes."""
    cache = Path(labels).with_suffix(".ds.npz")
    newest = max(Path(clip).stat().st_mtime, Path(labels).stat().st_mtime)
    if cache.exists() and cache.stat().st_mtime > newest:
        d = np.load(cache)
        return d["X"], d["Y"], d["F"]
    ball = {int(k): v for k, v in json.loads(Path(labels).read_text())["ball"].items()}
    X, Y, F = dataset(read_clip(clip), ball)
    np.savez(cache, X=X, Y=Y, F=F)
    return X, Y, F


def dataset(frames, ball):
    X, Y, F = [], [], []
    for i, cands, probs, _hit, patches in run_chain(frames, "models/ballnet/ballnet.onnx", keep_patches=True):
        if patches is None:
            continue
        near_label = any(abs(i - k) <= 3 for k in ball)
        for (x, y), p, patch in zip(cands, probs, patches):
            d = np.hypot(x - ball[i][0], y - ball[i][1]) if i in ball else np.inf
            if d <= POS_PX:
                label = 1
            elif d >= NEG_PX and (i in ball or not near_label) and p < 0.5:
                label = 0  # old-net positives outside labels are unchecked, leave them out
            else:
                continue
            X.append(np.rint(np.asarray(patch, np.float32).transpose(2, 0, 1) * 255.0))  # patches are 0..1
            Y.append(label)
            F.append(i)
    return np.array(X, np.uint8), np.array(Y, np.int64), np.array(F)


def cmd_train(args) -> None:
    import torch
    import torch.nn as nn

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from net import BallNet, augment

    device = "cuda" if torch.cuda.is_available() else "cpu"
    X, Y, F = cached_dataset(args.clip, args.labels)
    train = F < args.test_from if args.test_from else np.ones(len(Y), bool)
    for clip, labels in args.also:  # every frame of an extra clip trains
        X2, Y2, _ = cached_dataset(clip, labels)
        X, Y, train = np.concatenate([X, X2]), np.concatenate([Y, Y2]), np.concatenate([train, np.ones(len(Y2), bool)])
    print(f"skup: {int(Y.sum())} loptica, {int((Y == 0).sum())} ostalog; ucenje na {int(train.sum())} ({device})")

    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    net = BallNet()
    net.load_state_dict(torch.load("models/ballnet/net_final.pt", map_location="cpu"))
    net.to(device)
    opt = torch.optim.Adam(net.parameters(), args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    lossf = nn.BCEWithLogitsLoss()
    Xt, Yt = torch.from_numpy(X[train]), torch.from_numpy(Y[train].astype(np.float32))
    pos, neg = np.flatnonzero(Y[train] == 1), np.flatnonzero(Y[train] == 0)
    for epoch in range(args.epochs):
        net.train()
        idx = np.concatenate([neg, rng.choice(pos, max(1, len(neg) // 3))])
        rng.shuffle(idx)
        total = 0.0
        for s in range(0, len(idx), 256):
            b = idx[s : s + 256]
            xb = augment(Xt[b].float() / 255.0, rng).to(device)
            loss = lossf(net(xb), Yt[b].to(device))
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += loss.item() * len(b)
        sched.step()
        if (epoch + 1) % 5 == 0:
            print(f"  epoha {epoch + 1}/{args.epochs} loss {total / len(idx):.4f}", flush=True)
    net.eval().cpu()
    base = args.out if not args.test_from else args.out + "_split"
    torch.save(net.state_dict(), base + ".pt")
    torch.onnx.export(net, torch.zeros(1, 4, 32, 32), base + ".onnx", input_names=["x"], output_names=["logit"],
                      dynamic_axes={"x": {0: "n"}, "logit": {0: "n"}}, opset_version=13, dynamo=False)
    names = {"f.0": "c0", "f.3": "c1", "f.6": "c2", "h.1": "l0", "h.3": "l1"}
    np.savez(base + ".npz", **{names[k.rsplit(".", 1)[0]] + ("w" if k.endswith("weight") else "b"): v.numpy() for k, v in net.state_dict().items()})
    print(f"izvezeno {base}.onnx / .npz / .pt")


def camera_box(image: np.ndarray) -> tuple[int, int, int, int] | None:
    """The camera view inside a screen recording: the largest non-dark block below the browser bar."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    top, bottom = int(gray.shape[0] * 0.07), int(gray.shape[0] * 0.96)
    count, _labels, stats, _ = cv2.connectedComponentsWithStats((gray[top:bottom] > 30).astype(np.uint8), 4)
    if count <= 1:
        return None
    j = 1 + int(np.argmax(stats[1:, 4]))
    x, y, w, h, area = (int(v) for v in stats[j])
    if w < gray.shape[1] * 0.3 or not 1.5 <= w / max(1, h) <= 2.0:
        return None  # black screen or a dialog, not the 16:9 camera view
    return x, y + top, x + w, y + top + h


def cmd_convert(args) -> None:
    """Screen recording of the Supervisor camera view -> 640x480 TTCLIP like the robot writes."""
    capture = cv2.VideoCapture(args.clip)
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    out, box, skipped = [], None, 0
    while True:
        ok, image = capture.read()
        if not ok:
            break
        found = camera_box(image)
        if found is not None and (box is None or max(abs(a - b) for a, b in zip(found, box)) > 6):
            box = found  # the window moved or was resized
        if found is None or box is None:
            skipped += 1
            continue
        x0, y0, x1, y1 = box
        out.append(cv2.resize(image[y0 + 2 : y1 - 2, x0 + 2 : x1 - 2], (640, 480), interpolation=cv2.INTER_AREA))
    frames = np.stack(out)
    with open(args.labels, "wb") as handle:
        handle.write(HEAD.pack(b"TTCLIP01", 640, 480, len(frames), int(1e9 / fps)))
        handle.write(frames.tobytes())
    print(f"{args.labels}: {len(frames)} kadrova, {fps:.1f} fps, preskoceno {skipped} (crno ili prozor preko kamere)")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("convert", "labels", "eval", "train"):
        p = sub.add_parser(name)
        p.add_argument("clip")
        p.add_argument("labels")
    sub.choices["eval"].add_argument("--weights", default="models/ballnet/ballnet.onnx")
    sub.choices["eval"].add_argument("--from-frame", type=int, default=0)
    sub.choices["eval"].add_argument("--darker", action="store_true", help="also darker-than-background candidates")
    sub.choices["train"].add_argument("--out", default="models/ballnet/ballnet_robot")
    sub.choices["train"].add_argument("--test-from", type=int, default=0)
    sub.choices["train"].add_argument("--also", nargs=2, action="append", default=[], metavar=("CLIP", "LABELS"),
                                      help="another labeled clip, all of it trains")
    sub.choices["train"].add_argument("--epochs", type=int, default=30)
    sub.choices["train"].add_argument("--lr", type=float, default=6e-4)
    args = ap.parse_args()
    {"convert": cmd_convert, "labels": cmd_labels, "eval": cmd_eval, "train": cmd_train}[args.cmd](args)


if __name__ == "__main__":
    main()
