# IGRA (eldorado)

Hackathon game service for Agibot A2 Ultra: hand-gesture detection, announce test, leaderboard.

## Laptop quick test

```bash
cd eldorado
source .venv/bin/activate
# record samples if needed
python collect_gestures.py

# detection demo window
python detect_gestures.py

# full IGRA API (window + says "RADI" on stable match)
IGRA_SHOW_WINDOW=1 IGRA_ANNOUNCE_MODE=local python igra_api.py --port 8105
```

On a stable match against `gestures.json`, the service speaks **RADI** (macOS `say` / Linux espeak) and logs the label.

## Supervisor

Service type `igra` is registered and listed last in `config.example.yaml`.

1. Copy/merge the `igra` block into local `robot_supervisor_v2/config.yaml`
2. Start supervisor
3. Start **IGRA** from All Services
4. Open **Leaderboard** tab (empty until matches exist)

API proxies:

- `GET /api/igra/status`
- `GET /api/igra/leaderboard`

## Env knobs

| Var | Default | Meaning |
|---|---|---|
| `IGRA_CAMERA` | `0` | camera index/path |
| `IGRA_GESTURE_DB` | `eldorado/gestures.json` | MediaPipe samples |
| `IGRA_SHOW_WINDOW` | `1` standalone / `0` supervisor | OpenCV preview |
| `IGRA_ANNOUNCE_TEXT` | `RADI` | phrase on detect |
| `IGRA_ANNOUNCE_MODE` | `both` | `local` \| `agent` \| `both` |
| `IGRA_MAX_DISTANCE` | `0.6` | match threshold |
| `IGRA_STABLE_FRAMES` | `3` | frames before fire |

## Robot test

Same binary. Prefer `IGRA_SHOW_WINDOW=0` and `announce_mode: agent` once LiveKit conversation is up so the robot voice says **RADI**. Local TTS remains a fallback.
