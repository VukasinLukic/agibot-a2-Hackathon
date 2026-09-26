"""Candidate generator: ego-motion compensated three-frame difference."""
import cv2, numpy as np

FEAT_W = 320

class Aligner:
    def __init__(self):
        self.prev_small = None
    def step(self, gray):
        h, w = gray.shape
        s = cv2.resize(gray, (FEAT_W, int(FEAT_W * h / w)), interpolation=cv2.INTER_AREA)
        M = np.eye(2, 3, dtype=np.float32)
        if self.prev_small is not None:
            p = cv2.goodFeaturesToTrack(self.prev_small, 200, 0.01, 6)
            if p is not None and len(p) >= 12:
                q, st, _ = cv2.calcOpticalFlowPyrLK(self.prev_small, s, p, None, winSize=(15, 15), maxLevel=3)
                ok = st.ravel() == 1
                if ok.sum() >= 12:
                    A, _ = cv2.estimateAffinePartial2D(p[ok], q[ok], method=cv2.RANSAC, ransacReprojThreshold=0.7)
                    if A is not None:
                        A = A.astype(np.float32); A[:, 2] *= w / FEAT_W
                        M = A
        self.prev_small = s
        return M  # maps previous frame -> current frame

def compose(a, b):
    """a then b (both 2x3)."""
    A = np.vstack([a, [0, 0, 1]]); B = np.vstack([b, [0, 0, 1]])
    return (B @ A)[:2].astype(np.float32)

class CandidateSource:
    def __init__(self, width, height, thr=18, min_area=6, max_area=2500, compensate=True):
        self.w, self.h = width, height
        self.thr = thr; self.min_area = min_area; self.max_area = max_area
        self.al = Aligner() if compensate else None
        self.hist = []  # (gray, hsv, M_prev_to_this)
    def step(self, bgr):
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        M = self.al.step(g) if self.al else np.eye(2, 3, dtype=np.float32)
        self.hist.append((g, M))
        self.hist = self.hist[-3:]
        if len(self.hist) < 3:
            return [], None
        (g2, _), (g1, M1), (g0, M0) = self.hist  # g0 current
        M20 = compose(M1, M0)
        w1 = cv2.warpAffine(g1, M0, (self.w, self.h), borderMode=cv2.BORDER_REPLICATE)
        w2 = cv2.warpAffine(g2, M20, (self.w, self.h), borderMode=cv2.BORDER_REPLICATE)
        cur = g0.astype(np.int16)
        d1 = cur - w1; d2 = cur - w2
        # tolerate small misalignment: compare against local max of the warped previous frames
        k = np.ones((3, 3), np.uint8)
        d1m = cur - cv2.dilate(w1, k); d2m = cur - cv2.dilate(w2, k)
        brighter = np.minimum(d1m, d2m)
        self.last_bgr = bgr
        self.last_diff = np.clip(d1 + 128, 0, 255).astype(np.uint8)
        mask = (brighter > self.thr).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, lab, st, cen = cv2.connectedComponentsWithStats(mask)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        out = []
        for k_ in range(1, n):
            x, y, bw, bh, a = (int(v) for v in st[k_])
            if a < self.min_area or a > self.max_area:
                continue
            sl = (slice(y, y + bh), slice(x, x + bw))
            m = lab[sl] == k_
            hs = hsv[sl][m]
            br = brighter[sl][m]
            out.append(dict(x=float(cen[k_][0]), y=float(cen[k_][1]), bw=bw, bh=bh, area=a,
                            fill=a / (bw * bh), sat=float(hs[:, 1].mean()), val=float(hs[:, 2].mean()),
                            contrast=float(br.mean()), cmax=float(br.max())))
        return out, mask


P = 32
SIDE_K = 1.6
def patch_side(c):
    return max(24.0, SIDE_K * max(c['bw'], c['bh']))

def extract_patches(bgr, diff, cands):
    """N x 4 x P x P float32: BGR of the current frame and the signed diff to the previous one."""
    out = np.zeros((len(cands), 4, P, P), np.float32)
    stack = np.dstack([bgr, diff])
    for i, c in enumerate(cands):
        side = patch_side(c)
        M = np.float32([[side / P, 0, c['x'] - side / 2], [0, side / P, c['y'] - side / 2]])
        p = cv2.warpAffine(stack, M, (P, P), flags=cv2.INTER_AREA | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
        out[i] = p.transpose(2, 0, 1) / 255.0
    return out
