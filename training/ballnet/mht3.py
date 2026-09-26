"""Online ball tracker v3: greedy multi-hypothesis association + tracklet-level output gating.

Same interface as mht2: Tracker(**kw).step(t, cands, probs) -> None or
('observed'|'predicted', x, y, track_id, score).

Changes vs mht2
- Association: velocity follows the newest measurement more (v_alpha 0.9, ball bounces/hits change
  velocity abruptly), small gravity term in prediction, shorter track memory (max_miss 3).
- Output decision is a per-tracklet score instead of "best quality, almost always output":
    score = (1-w_last)*mean(logit over last k hits) + w_last*logit(last hit)
          + w_sp * clip(log(net_speed / sp_ref), sp_lo, sp_hi)        # ball flies, arms/badges crawl
          - n1_pen                                   (single-hit tracklet, no motion evidence yet)
          - w_edge * max(0, 1 - edge_dist/edge_px)   (frame-border motion artefacts)
          + w_size * min(0, log(size / size_ref))    (tiny blobs are rarely the ball)
  net_speed = displacement between first and last hit of the window / elapsed frames, so oscillating
  objects (swinging badges, waving hands) score low even when their per-frame motion is large.
- Hysteresis: a new tracklet must reach thr_new; the tracklet that was output within the last
  conf_hold frames only needs thr_conf and gets conf_bonus when competing with others.
- Only the confirmed track may coast ('predicted', up to `coast` missed frames).
Pixel parameters (speeds, sizes, gates, edge) assume 960x540 frames at 30 fps.
"""
import math

DEFAULT = dict(
    # association
    p_new=0.2, u_min=-4.0, gate_new=90.0, gate_base=30.0, gate_speed=0.8, gate_grow=10.0,
    w_unary=0.12, max_miss=3, v_alpha=0.9, grav=1.0,
    # tracklet score
    k=5, w_last=0.7, sp_ref=20.0, w_sp=1.0, sp_lo=-1.5, sp_hi=1.0, n1_pen=1.0,
    w_edge=2.0, edge_px=40.0, w_size=2.0, size_ref=25.0,
    # output
    thr_new=1.5, thr_conf=-1.5, conf_hold=3, conf_bonus=0.5, coast=2,
    width=960, height=540,
)


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4); return math.log(p / (1 - p))


class Tracklet:
    __slots__ = ('id', 'hits', 'x', 'y', 'vx', 'vy', 'last_t', 'misses')
    _next = 0

    def __init__(self, t, c, u):
        self.id = Tracklet._next; Tracklet._next += 1
        self.hits = [(t, c['x'], c['y'], u, max(c['bw'], c['bh']))]
        self.x, self.y, self.vx, self.vy = c['x'], c['y'], 0.0, 0.0
        self.last_t = t; self.misses = 0

    def predict(self, t, g):
        dt = t - self.last_t
        if len(self.hits) < 2: return self.x, self.y
        return self.x + self.vx * dt, self.y + self.vy * dt + 0.5 * g * dt * dt

    def gate(self, t, P):
        if len(self.hits) == 1: return P['gate_new']
        dt = t - self.last_t
        return P['gate_base'] + P['gate_speed'] * math.hypot(self.vx, self.vy) * dt + P['gate_grow'] * (dt - 1)

    def update(self, t, c, u, P):
        dt = t - self.last_t
        nvx = (c['x'] - self.x) / dt; nvy = (c['y'] - self.y) / dt
        if len(self.hits) == 1:
            self.vx, self.vy = nvx, nvy
        else:
            a = P['v_alpha']
            self.vx = (1 - a) * self.vx + a * nvx
            self.vy = (1 - a) * (self.vy + P['grav'] * dt) + a * nvy
        self.x, self.y = c['x'], c['y']; self.last_t = t; self.misses = 0
        self.hits.append((t, c['x'], c['y'], u, max(c['bw'], c['bh'])))
        if len(self.hits) > 8: del self.hits[0]


class Tracker:
    def __init__(self, **kw):
        unknown = set(kw) - set(DEFAULT)
        if unknown: raise TypeError(f'unknown tracker params: {sorted(unknown)}')
        self.P = dict(DEFAULT); self.P.update(kw)
        self.u_new = logit(self.P['p_new'])
        self.tr = []; self.conf = None; self.conf_t = -10 ** 9

    # ---- association (greedy on cost = normalised distance - w_unary*logit - length bonus) ----
    def _associate(self, t, cands, us):
        P = self.P; g = P['grav']; umin = P['u_min']; wu = P['w_unary']
        valid = [ci for ci in range(len(cands)) if us[ci] > umin]
        pairs = []
        for ti, tr in enumerate(self.tr):
            px, py = tr.predict(t, g); gt = tr.gate(t, P); g2 = gt * gt
            lb = 0.03 * min(len(tr.hits), 8)
            for ci in valid:
                c = cands[ci]; dx = c['x'] - px
                if dx * dx >= g2: continue
                dy = c['y'] - py; d2 = dx * dx + dy * dy
                if d2 < g2: pairs.append((math.sqrt(d2) / gt - wu * us[ci] - lb, ti, ci))
        pairs.sort()
        used_t, used_c = set(), set()
        for _, ti, ci in pairs:
            if ti in used_t or ci in used_c: continue
            self.tr[ti].update(t, cands[ci], us[ci], P); used_t.add(ti); used_c.add(ci)
        keep = []
        for ti, tr in enumerate(self.tr):
            if ti not in used_t: tr.misses += 1
            if tr.misses <= P['max_miss']: keep.append(tr)
        self.tr = keep
        for ci in valid:
            if ci not in used_c and us[ci] >= self.u_new:
                self.tr.append(Tracklet(t, cands[ci], us[ci]))

    # ---- tracklet score (see module docstring) ----
    def score(self, tr):
        P = self.P; h = tr.hits[-P['k']:]; n = len(h)
        s = (1 - P['w_last']) * (sum(x[3] for x in h) / n) + P['w_last'] * h[-1][3]
        if n == 1:
            s -= P['n1_pen']
        else:
            sp = math.hypot(h[-1][1] - h[0][1], h[-1][2] - h[0][2]) / (h[-1][0] - h[0][0])
            s += P['w_sp'] * min(max(math.log(max(sp, 1.0) / P['sp_ref']), P['sp_lo']), P['sp_hi'])
        if P['w_edge']:
            e = min(tr.x, P['width'] - 1 - tr.x, tr.y, P['height'] - 1 - tr.y)
            if e < P['edge_px']: s -= P['w_edge'] * (1 - max(e, 0.0) / P['edge_px'])
        if P['w_size']:
            s += P['w_size'] * min(math.log(max(h[-1][4], 1) / P['size_ref']), 0.0)
        return s

    def step(self, t, cands, probs):
        P = self.P
        us = [logit(p) for p in probs]
        self._associate(t, cands, us)
        conf = self.conf if (self.conf is not None and t - self.conf_t <= P['conf_hold']) else None
        best = None
        for tr in self.tr:
            if tr.last_t != t: continue
            s = self.score(tr)
            is_conf = tr is conf
            if s < (P['thr_conf'] if is_conf else P['thr_new']): continue
            key = s + (P['conf_bonus'] if is_conf else 0.0)
            if best is None or key > best[0]: best = (key, s, tr)
        if best is not None:
            _, s, tr = best; self.conf = tr; self.conf_t = t
            return ('observed', tr.x, tr.y, tr.id, s)
        if conf is not None and conf.misses and conf.misses <= P['coast'] and conf in self.tr:
            px, py = conf.predict(t, P['grav'])
            return ('predicted', px, py, conf.id, None)
        return None
