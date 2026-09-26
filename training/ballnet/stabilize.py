"""Warp every frame of a handheld recording onto one reference frame (background features)."""
import sys

import cv2
import numpy as np

src, dst, ref_s = sys.argv[1], sys.argv[2], float(sys.argv[3])
cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS)
W, H = int(cap.get(3)), int(cap.get(4))
orb = cv2.ORB_create(3000)
bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
S = 0.5


def feats(img):
    g = cv2.cvtColor(cv2.resize(img, None, fx=S, fy=S), cv2.COLOR_BGR2GRAY)
    return orb.detectAndCompute(g, None)


cap.set(cv2.CAP_PROP_POS_MSEC, ref_s * 1000)
ok, ref = cap.read()
k0, d0 = feats(ref)
cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
out = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*"MJPG"), fps, (W, H))
out.set(cv2.VIDEOWRITER_PROP_QUALITY, 95)
n = bad = 0
M_last = np.eye(2, 3, dtype=np.float32)
while True:
    ok, img = cap.read()
    if not ok:
        break
    k, d = feats(img)
    M = None
    if d is not None:
        m = sorted(bf.match(d, d0), key=lambda x: x.distance)[:500]
        if len(m) >= 30:
            a = np.float32([k[x.queryIdx].pt for x in m]) / S
            b = np.float32([k0[x.trainIdx].pt for x in m]) / S
            M, inl = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=3.0)
            if M is None or inl.sum() < 25:
                M = None
    if M is None:
        bad += 1
        M = M_last
    M_last = M
    out.write(cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT))
    n += 1
out.release()
print(f"frames {n}, bez poravnanja {bad}, fps {fps:.2f}")
