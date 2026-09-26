import cv2, numpy as np, sys
src = r'C:\Users\Tea\Downloads\IMG_5844.MOV'
W, H = 960, 540
c = cv2.VideoCapture(src)
n = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
mm = np.lib.format.open_memmap('frames960.npy', mode='w+', dtype=np.uint8, shape=(n, H, W, 3))
ts = []
i = 0
while True:
    ok, f = c.read()
    if not ok: break
    ts.append(c.get(cv2.CAP_PROP_POS_MSEC))
    mm[i] = cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA)
    i += 1
mm.flush()
np.save('ts.npy', np.array(ts[:i]))
print(i, n, ts[:3], ts[-1])
