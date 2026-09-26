"""Bounce / hit events from a ball track (frame -> (x, y)). Causal with a 1-sample look-ahead."""
import math

def detect(track, max_gap=2, min_turn_v=4.0, min_hit_vx=5.0):
    ts = sorted(track)
    ev = []
    for i in range(1, len(ts) - 1):
        a, b, c = ts[i - 1], ts[i], ts[i + 1]
        if b - a > max_gap or c - b > max_gap: continue
        (xa, ya), (xb, yb), (xc, yc) = track[a], track[b], track[c]
        vx1, vy1 = (xb - xa) / (b - a), (yb - ya) / (b - a)
        vx2, vy2 = (xc - xb) / (c - b), (yc - yb) / (c - b)
        if vx1 * vx2 < 0 and min(abs(vx1), abs(vx2)) >= min_hit_vx:
            ev.append((b, 'hit', round(xb), round(yb)))
        elif vy1 >= min_turn_v and vy2 <= -min_turn_v:
            ev.append((b, 'bounce', round(xb), round(yb)))
    # a hit and a bounce one frame apart collapse to the stronger one (hit)
    out = []
    for e in ev:
        if out and e[0] - out[-1][0] <= 1 and out[-1][1] != e[1]:
            if e[1] == 'hit': out[-1] = e
            continue
        out.append(e)
    return out

if __name__ == '__main__':
    import json, sys
    g = {int(k): (v[0], v[1]) for k, v in json.load(open(sys.argv[1]))['gt'].items()}
    for e in detect(g): print(e)
