"""Više hipoteza nad kandidatima sa skorom (mht3). Unarni član je logit iz ``BallNet``-a.

Povezivanje: pohlepno po ceni (udaljenost / gate, minus skor, minus dužina
traga). Brzina prati najnovije merenje (``v_alpha``), predikcija ima malu
gravitaciju. Nepovezan kandidat iznad ``p_new`` otvara novu hipotezu.

Izlaz nije "najbolja hipoteza uvek", nego skor po tracklet-u:

- logit (sredina poslednjih ``k`` i poslednji pogodak),
- brzina između prvog i poslednjeg pogotka u prozoru (loptica leti; ruke i
  bedževi osciluju, pa im je neto brzina mala),
- kazna za jedan pogodak, za blizinu ivice kadra i za sitan okvir.

Histereza: nova hipoteza mora preći ``thr_new``, potvrđena (izlaz u poslednjih
``conf_hold`` kadrova) samo ``thr_conf`` i dobija ``conf_bonus``. Samo potvrđena
sme da se nastavi kao ``predicted`` (najviše ``coast`` promašaja).

Vreme ``t`` je u kadrovima od 30 fps. Pikseli važe na širini 960 (kadar
960 × 540); ``TrackerParams.for_frame`` ih skalira na radni kadar.
``thr_new`` se ponovo bira za svaku novu težinu mreže.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Protocol, Sequence


class _Point(Protocol):
    x: float
    y: float
    bw: int
    bh: int


@dataclass(frozen=True, slots=True)
class TrackerParams:
    # povezivanje
    p_new: float = 0.2
    u_min: float = -4.0
    gate_new: float = 90.0
    gate_base: float = 30.0
    gate_speed: float = 0.8
    gate_grow: float = 10.0
    w_unary: float = 0.12
    w_length: float = 0.03
    max_miss: int = 3
    v_alpha: float = 0.9
    grav: float = 1.0
    # skor tracklet-a
    k: int = 5
    w_last: float = 0.7
    sp_ref: float = 20.0
    w_sp: float = 1.0
    sp_lo: float = -1.5
    sp_hi: float = 1.0
    n1_pen: float = 1.0
    w_edge: float = 2.0
    edge_px: float = 40.0
    w_size: float = 2.0
    size_ref: float = 25.0
    # izlaz
    thr_new: float = 1.5
    thr_conf: float = -1.5
    conf_hold: int = 3
    conf_bonus: float = 0.5
    coast: int = 2
    # radni kadar (ivica)
    width: float = 960.0
    height: float = 540.0

    def for_frame(self, width: int, height: int, ref_width: float = 960.0) -> TrackerParams:
        """Pikseli (gate, brzina, gravitacija, veličina, ivica) za radni kadar ``width × height``."""
        factor = width / ref_width
        return replace(
            self,
            gate_new=self.gate_new * factor,
            gate_base=self.gate_base * factor,
            gate_grow=self.gate_grow * factor,
            grav=self.grav * factor,
            sp_ref=self.sp_ref * factor,
            edge_px=self.edge_px * factor,
            size_ref=self.size_ref * factor,
            width=float(width),
            height=float(height),
        )


@dataclass(frozen=True, slots=True)
class TrackHit:
    kind: str
    x: float
    y: float
    track_id: int
    score: float | None
    prob: float


@dataclass(frozen=True, slots=True)
class _Hit:
    t: float
    x: float
    y: float
    unary: float
    size: int
    prob: float


_MEMORY = 8


class Tracklet:
    __slots__ = ("id", "hits", "x", "y", "vx", "vy", "last_t", "misses")

    def __init__(self, track_id: int, t: float, candidate: _Point, unary: float, prob: float) -> None:
        self.id = track_id
        self.hits = [_Hit(t, candidate.x, candidate.y, unary, max(candidate.bw, candidate.bh), prob)]
        self.x = candidate.x
        self.y = candidate.y
        self.vx = 0.0
        self.vy = 0.0
        self.last_t = t
        self.misses = 0

    def predict(self, t: float, grav: float) -> tuple[float, float]:
        if len(self.hits) < 2:
            return self.x, self.y
        dt = t - self.last_t
        return self.x + self.vx * dt, self.y + self.vy * dt + 0.5 * grav * dt * dt

    def gate(self, t: float, params: TrackerParams) -> float:
        if len(self.hits) == 1:
            return params.gate_new
        dt = t - self.last_t
        return params.gate_base + params.gate_speed * math.hypot(self.vx, self.vy) * dt + params.gate_grow * (dt - 1)

    def update(self, t: float, candidate: _Point, unary: float, prob: float, params: TrackerParams) -> None:
        dt = max(t - self.last_t, 1e-3)
        vx = (candidate.x - self.x) / dt
        vy = (candidate.y - self.y) / dt
        if len(self.hits) == 1:
            self.vx, self.vy = vx, vy
        else:
            alpha = params.v_alpha
            self.vx = (1 - alpha) * self.vx + alpha * vx
            self.vy = (1 - alpha) * (self.vy + params.grav * dt) + alpha * vy
        self.x = candidate.x
        self.y = candidate.y
        self.last_t = t
        self.misses = 0
        self.hits.append(_Hit(t, candidate.x, candidate.y, unary, max(candidate.bw, candidate.bh), prob))
        if len(self.hits) > _MEMORY:
            del self.hits[0]


class Tracker:
    """``step(t, kandidati, skorovi)`` -> ``TrackHit`` ili ``None``. Ne zna ništa o stolu."""

    def __init__(self, params: TrackerParams | None = None) -> None:
        self.params = params or TrackerParams()
        self.tracklets: list[Tracklet] = []
        self._u_new = _logit(self.params.p_new)
        self._next_id = 0
        self._confirmed: Tracklet | None = None
        self._confirmed_t = -math.inf

    def step(self, t: float, candidates: Sequence[_Point], probs: Sequence[float]) -> TrackHit | None:
        params = self.params
        unaries = [_logit(p) for p in probs]
        self._associate(t, candidates, unaries, probs)
        confirmed = self._confirmed if t - self._confirmed_t <= params.conf_hold else None
        best: tuple[float, float, Tracklet] | None = None
        for track in self.tracklets:
            if track.last_t != t:
                continue
            score = self.score(track)
            is_confirmed = track is confirmed
            if score < (params.thr_conf if is_confirmed else params.thr_new):
                continue
            key = score + (params.conf_bonus if is_confirmed else 0.0)
            if best is None or key > best[0]:
                best = (key, score, track)
        if best is not None:
            _key, score, track = best
            self._confirmed = track
            self._confirmed_t = t
            return TrackHit("observed", track.x, track.y, track.id, score, track.hits[-1].prob)
        if confirmed is not None and 0 < confirmed.misses <= params.coast and confirmed in self.tracklets:
            x, y = confirmed.predict(t, params.grav)
            return TrackHit("predicted", x, y, confirmed.id, None, confirmed.hits[-1].prob)
        return None

    def score(self, track: Tracklet) -> float:
        params = self.params
        hits = track.hits[-params.k :]
        last = hits[-1]
        score = (1 - params.w_last) * (sum(hit.unary for hit in hits) / len(hits)) + params.w_last * last.unary
        if len(hits) == 1:
            score -= params.n1_pen
        else:
            first = hits[0]
            speed = math.hypot(last.x - first.x, last.y - first.y) / max(last.t - first.t, 1e-3)
            prior = math.log(max(speed, 1e-6) / params.sp_ref)
            score += params.w_sp * min(max(prior, params.sp_lo), params.sp_hi)
        if params.w_edge:
            edge = min(track.x, params.width - 1 - track.x, track.y, params.height - 1 - track.y)
            if edge < params.edge_px:
                score -= params.w_edge * (1 - max(edge, 0.0) / params.edge_px)
        if params.w_size:
            score += params.w_size * min(math.log(max(last.size, 1) / params.size_ref), 0.0)
        return score

    def _associate(self, t: float, candidates: Sequence[_Point], unaries: list[float], probs: Sequence[float]) -> None:
        params = self.params
        valid = [ci for ci, unary in enumerate(unaries) if unary > params.u_min]
        pairs: list[tuple[float, int, int]] = []
        for ti, track in enumerate(self.tracklets):
            px, py = track.predict(t, params.grav)
            gate = track.gate(t, params)
            gate2 = gate * gate
            bonus = params.w_length * min(len(track.hits), _MEMORY)
            for ci in valid:
                dx = candidates[ci].x - px
                dy = candidates[ci].y - py
                dist2 = dx * dx + dy * dy
                if dist2 < gate2:
                    pairs.append((math.sqrt(dist2) / gate - params.w_unary * unaries[ci] - bonus, ti, ci))
        pairs.sort()
        used_t: set[int] = set()
        used_c: set[int] = set()
        for _cost, ti, ci in pairs:
            if ti in used_t or ci in used_c:
                continue
            self.tracklets[ti].update(t, candidates[ci], unaries[ci], float(probs[ci]), params)
            used_t.add(ti)
            used_c.add(ci)
        kept: list[Tracklet] = []
        for ti, track in enumerate(self.tracklets):
            if ti not in used_t:
                track.misses += 1
            if track.misses <= params.max_miss:
                kept.append(track)
        self.tracklets = kept
        for ci in valid:
            if ci not in used_c and unaries[ci] >= self._u_new:
                self.tracklets.append(Tracklet(self._next_id, t, candidates[ci], unaries[ci], float(probs[ci])))
                self._next_id += 1


def _logit(p: float) -> float:
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))
