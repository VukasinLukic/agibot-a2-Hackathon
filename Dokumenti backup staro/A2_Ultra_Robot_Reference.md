# A2 Ultra — Robot Reference for Development (Comtrade x Agibot Hackathon)

> This document is a self-contained technical reference for developing on the **Agibot A2 Ultra** humanoid robot during the Comtrade/Agibot hackathon. It is written for an AI coding assistant (Claude Code) to use as ground-truth context while writing code for this robot. It does not assume any specific project idea — it only covers how the robot and its software work.
>
> Source: organizer-provided documentation (`Agibot_A2_Ultra_Documentation`, `A2_AimRT_Usage`, `Agibot_Network_Communication_Documentation`, `Agibot_Safety_Guide`, `A2_PC_Nvidia_Jetson_Agx_Orin_Docs`), consolidated and cross-checked. Places where the source documents contradict each other are explicitly flagged with ⚠️ — verify those with a mentor on-site rather than trusting either version blindly.

---

## 0. Quick reference (TL;DR)

| Thing | Value |
|---|---|
| Ethernet login | `ssh agi@192.168.2.50` |
| From there, jump to dev PC | `ssh agi@192.168.100.110` |
| Dev PC (PC2, Jetson Orin AGX) | `192.168.100.110` — **write and run all custom code here** |
| Motor/hardware PC (PC1, x86) | `192.168.100.100` — **never develop here, only call it** |
| Supervisor web UI (Ethernet) | `http://192.168.2.50:8070` |
| Supervisor start command | `cd humanoid-platform && ./run_robot_supervisor_v2.sh` |
| AimDK call shape | `POST http://<host>:<port>/rpc/aimdk.protocol.<Service>/<Method>` + JSON body, header required (can be `{}`) |
| Navigation/general services | Route to PC2 gateway, port `51056` |
| Motor/arm control services | Route to PC1 Motor Controller, port `56322` (Arm Planner: `56321`) |
| Locomotion velocity stream | UDP, PC1, port `50041` |
| Sensor data | ROS2 topics, **Domain ID 232** (not the default 231!) |
| First debug command, always | `aima em doctor` |
| Free/restart a stuck module | `aima em stop-app <name>` / `start-app` / `reset-app` |
| Identify which PC you're on | `hostname -I` (NOT `whoami` — useless) |

---

## 1. What this robot physically is

- Humanoid, **169 cm tall, ~70 kg**.
- **40 degrees of freedom**; full arm and head motion; general doc claims the head can turn up to 120° (see verified note below — the head almost certainly does move, but the exact angle is probably smaller than 120°).
- **Functional arms** — can grasp, open doors, etc. This is the A2's headline advantage over the X2.
- **360° LiDAR** (Livox Mid-360), mounted in the neck area, facing downward. Range: ~40m in poor conditions (10% reflectivity), ~70m in good conditions (80% reflectivity). Used for mapping AND as a safety layer (robot refuses actions / holds position if something is too close).
- Cameras: 2 fisheye + 1 central RGB on chest; 1 camera under the neck (~45° downward); 1 camera in the pelvis/groin area (~45° downward).
- Small screen (105.5×67.2×3mm housing, 95.04×53.86mm visible) — can show text/animations/custom content.
- Microphone array in the chest.
- RGB status lights (chest + head sides): **blue = normal, red = low battery, green = charging**.
- Bluetooth-connected tablet for manual control / setup (much of its functionality must be disabled during development, per organizer docs).
- Battery life: **1.5–3 hours** depending on activity.

**Head rotation — resolved, most likely correct info**: the general A2 documentation states the robot "can turn its head 120 degrees," while the AimRT/AimDK document's own joint table lists the **Head as "Fixed on the `with_casing` variant."** These conflict directly. Checked against public specs: multiple independent commercial listings of the A2 Ultra consistently break its 40 DOF down as **2 DOF at the neck/head** + 14 (arms) + 12 (legs) + 12 (hands) = 40 — so **the head almost certainly does move** (2 joints: likely yaw + pitch), and the AimDK doc's "fixed" note is most likely describing one specific hardware variant/config, not the standard A2 Ultra. On the exact rotation angle: AgiBot's own official documentation for the closely-related A2-Lite model specifies the neck's functional joint range as **-0.785 to 0.785 rad (≈ ±45°, ~90° total)** via joints named `idx27_head_joint1`/`idx28_head_joint2` (only the first is functional; the second must be sent but is ignored). This is a different exact model than the A2 Ultra used at the hackathon, so treat "~45° each way" as the best current estimate rather than a certainty — **"120°" is probably an overstatement, and "fixed" is probably wrong.** This takes 10 seconds to confirm by just watching the robot turn its head in person — worth doing before building anything that depends on the exact range.

---

## 2. The two on-board computers

| | PC1 | PC2 |
|---|---|---|
| Hardware | x86 | NVIDIA Jetson Orin AGX (64GB RAM) |
| Role | Motor controller, network card, sensor readings, screen control | Development, compute-heavy work (vision, AI models, TensorRT) |
| OS | Minimal, low storage (few GB) | Ubuntu 22.04, JetPack 6.0 |
| Address | `192.168.100.100` | `192.168.100.110` |
| Do you write code here? | **No — only call it via AimDK.** It exists so critical motor/safety operations run fast and isolated. | **Yes — this is the dev machine.** |

**Why this split matters practically**: any code you write runs on PC2. It never talks to motors directly — it sends an AimDK request, which is routed to PC1, which actually moves the hardware. If PC2 crashes or stops responding mid-navigation, PC1 will notice and stop the robot on its own rather than let it run open-loop.

---

## 3. Networking

### Ethernet (recommended, most reliable)
- The A2 has a **single Ethernet entry point**: `agi@192.168.2.50` (this is PC1's exposed address for external connections).
- From a laptop plugged in via Ethernet, `ssh agi@192.168.2.50` gets you onto PC1.
- To reach the dev machine (PC2) from there: `ssh agi@192.168.100.110`.
- Password for first login (as of the source docs): `1`. If it doesn't work, ask the mentor/admin on duty.

### WiFi
- PC1 is the WiFi connection hub for the A2 (not PC2, unlike the X2).
- Method: connect via Ethernet first (`ssh agi@192.168.100.100` — note this may already be where you land), then run `ip addr` to find the current WiFi address (it changes daily, network is typically "Comtrade Guest"). From there you can reconnect over WiFi later, then jump to PC2 (`ssh agi@192.168.100.110`).

### VS Code Remote / SSH tunneling
For the A2, you need a **jump connection** (since PC2 isn't directly reachable from outside):
```
ssh -J agi@<wifi_or_ethernet_address> agi@192.168.100.110
```
Add this as a host in VS Code's Remote Explorer. First connection will ask OS type — answer **Linux**. You'll need both PC1 and PC2 passwords.

### Identifying which PC you're on
- `whoami` is useless (returns a generic/unhelpful result).
- Use `hostname -I` (preferred) or `hostname`. PC1 will show an address like `192.168.100.100`; PC2 will show `192.168.100.110`.

---

## 4. Software stack — how a command actually reaches the robot

Four conceptual layers, top to bottom:

1. **Your code** (Python, or anything that can make HTTP requests) — runs on PC2.
2. **Supervisor** (Comtrade's software) — optional layer with ready-made features (speech, vision, navigation with checkpoints). You can call into it, extend it, or bypass it entirely and call AimDK directly.
3. **AimDK** (Agibot's software) — ~210 HTTP-RPC service definitions. This is what actually exposes "move arm", "get robot state", "navigate to point", etc. as callable endpoints.
4. **AimRT** — the transport/messaging layer underneath AimDK (like a publisher-subscriber bus). You never call it directly; it just needs to be running (it will be).

### Supervisor
- Located in the `humanoid-platform` folder. Start with `./run_robot_supervisor_v2.sh`.
- Web UI reachable at `http://192.168.2.50:8070` (Ethernet).
- **Speech/Voice Agent module**: STT (Soniox) → Azure OpenAI API (with optional RAG call) → response → TTS (Soniox or ElevenLabs, selectable). Persona/speech style is configurable here. The robot can also be told to say fixed lines without invoking the full AI pipeline.
- **Computer Vision module**: human/face/object detection. Uses **YOLOv26** for human detection by default (an RT-DETR custom model exists but is in development/less reliable — prefer YOLO). Face recognition uses a facenet-based pipeline: detect human → CNN detects face within the bounding box → extract facial embedding → store alongside a name in a **local SQLite database**. The robot only stores a name if explicitly asked to remember someone. When this Vision Controller is active, manual activation is disabled — the robot starts conversation autonomously on detecting a human.
- **Navigation module**: give the robot checkpoints; it can speak lines or trigger custom scripts on arrival. Requirements before navigation works (see §7).
- **Misc functions**: mostly unused placeholders (time, weather) — can be extended via `agent_main.py`.
- Backend extras mentioned in team notes: a **LiveKit server** for voice/streaming, and a **self-hosted Qdrant vector database** running on the robot (used for persona / RAG-style knowledge retrieval).

### AimDK — the actual call shape
```
POST http://<address>:<port>/rpc/aimdk.protocol.<Service_Name>/<Method_Name>
Content-Type: application/json
Body: {"header": {...}}   <- header is REQUIRED, even if empty {}
```
Example (get current navigation/action state, from PC2 gateway):
```bash
curl -s -X POST http://192.168.100.110:51056/rpc/aimdk.protocol.PncService/ActionGetState \
  -H 'Content-type: application/json' -d '{"header":{}}'
```
- Can be called from any language capable of HTTP requests (curl, Python `requests`, JS `fetch`, etc.).
- **Routing matters**: navigation/mapping/general services live on PC2 (gateway port `51056`); motor control and hardware modules live on PC1 (Motor Controller port `56322`, Arm Planner port `56321`). Locomotion velocity is a separate UDP stream on PC1 port `50041`. Sensor data comes over ROS2 topics on **Domain ID 232** (default of 231 is wrong).
- A response of `HTTP 200, code: 0` only means **the request was received** — not that the robot did anything. Always verify actual state afterward (poll `GetState`, `GetAction`, etc.).
- "Resource not found" usually means you targeted the wrong PC/port for that service.

### Debugging AimDK
- `aima em doctor` — lists all running processes, their status and PID. **Always run this first** when something isn't working.
- `aima em stop-app <app_name>` / `start-app` / `reset-app` — free up or restart a resource AimDK is holding onto. By default, the Supervisor already stops the `agent` module to free resources for itself.
- AimDK documentation can be out of date; it also tends to fail silently (returns a "received" status instead of an error). If something seems broken, ask a mentor rather than assuming your code is wrong.

---

## 5. The three "gates" — why commands silently do nothing

The robot will **silently ignore** any command sent while it isn't in the right state, rather than returning an error. Every motion command must pass three checks in order:

### Gate 1 — Control source
The request header's `control_source` field must be `ControlSource_SAFE` (checked via `McBaseService/GetWorkMode`). This exists so a stray/unauthorized command can't move the robot.

### Gate 2 — Action mode
`McActionService/GetAction` returns the robot's current action, encoded as a composite string that describes BOTH leg behavior and arm interface simultaneously, e.g.:
```
McAction_RL_LOCOMOTION_ARM_EXT_PLANNING_MOVE
```
| Prefix (legs) | Meaning |
|---|---|
| `PASSIVE_UPPER_BODY_*` | Motors off — **do not use this without the gantry** |
| `RL_LOCOMOTION_ARM_EXT_*` | Balancing and walking |
| `RL_WHOLE_BODY_EXT_*` | Whole-body external control |
| `DEFAULT` | Post-bootup; accepts nothing |

| Suffix (arm interface) | Accepts | Rejects |
|---|---|---|
| `PLANNING_MOVE` | PlanningMove (point-to-point) | Servo streams |
| `JOINT_SERVO` | Continuous joint-target stream | PlanningMove |
| `ONLINE_PLANNING` | Streamed trajectory queue | PlanningMove |

You **cannot jump arbitrarily between modes** — there's a predefined path, and illegal jumps are silently blocked. In the Supervisor, the **"Arm" button** (in the Navigation section) puts the robot into a 100%-ready-for-walking mode, usable even without triggering navigation.

Check errors in the Motor Controller log (`/agibot/log/pnc_arm/pnc_arm.log`), which will show things like:
```
[Warn][PncArmModule][pnc_arm_service.cc:85] current_action not right: RL_WHOLE_BODY_EXT_JOINT_SERVO
```

### Gate 3 — Planner bounds
Every joint value in a request must be within the **planner's** range (not the URDF's raw range — the planner range is authoritative and can be stricter). If any single joint is out of bounds, the **entire** command is discarded.

Example bounds:
| Joint | Planner range |
|---|---|
| Joint 1 (shoulder flexion) | -2.91 → 2.91 |
| Joint 4 right (elbow) | 0.03 → 2.00 |
| Joint 4 left (elbow) | -2.00 → -0.03 |

Note: the elbow can **never** be 100% straight (range doesn't include 0).

Error format in the Motor Controller log:
```
BoundsChecker: Invalid joint[3]: range: 0.03 2 q: 0 check goal: BoundsChecker is invalid
```

### Debugging table for the gates
| Symptom | Gate to check | Where |
|---|---|---|
| HTTP 200, code 0, nothing happens | 1 or 2 | `GetWorkMode`, `GetAction` |
| `'current_action not right'` | 2 | `/agibot/log/pnc_arm/pnc_arm.log` |
| `'BoundsChecker is invalid'` | 3 | same log |
| HTTP 500, code 1002 | — | RPC registered but not implemented in this build (`JointControl`, `SafeStop`, `GetWorkState` are known examples) |
| HTTP 404 | — | No such method exists |
| `task_id` always `0` | — | MC proxies arm moves to `pnc_arm:56321` and never returns a real id — verify by polling `GetJointState` for convergence instead |

---

## 6. Joints (26 total, addressed by index)

| Group | Index range | Notes |
|---|---|---|
| Legs | 1–12 (6 per leg) | hip_roll, hip_yaw, hip_pitch, tarsus, toe_pitch, toe_roll |
| Left arm | 13–19 (7 joints) | |
| Right arm | 20–26 (7 joints) | |
| Head | not in this indexed list | Most likely 2 separate joints (yaw + pitch, ~±45° each based on public AgiBot specs), controlled via its own dedicated interface rather than idx 1–26 — see §1 for the "fixed vs. movable" note. Don't assume it's absent just because it's not indexed here. |

Arm joints, in `base_link` frame (x = forward, y = left, z = up):

| Joint | What it is | Notes |
|---|---|---|
| joint1 | Shoulder flexion | 0 = hanging; +1.5708 = horizontal forward; same sign both arms; range [-2.91, 2.91] |
| joint2 | Shoulder abduction | Home = +1.26 (left), -1.26 (right) |
| joint3 | Upper arm roll | |
| joint4 | Elbow | [0.03, 2.0] right; [-2.0, -0.03] left |
| joint5–7 | Wrist | Parallel linkage, not in the URDF serial chain |

### Request shape for arm/body motion (`McMotionService/PlanningMove`)
```json
{
  "header": {
    "timestamp": {},
    "control_source": "ControlSource_SAFE"
  },
  "group": "McPlanningGroup_DUAL_ARM",
  "mode": "McPlanningMode_DEFAULT",
  "target": {
    "type": "JOINT",
    "joints": [ /* 7 or 14 values, radians */ ]
  },
  "param": {
    "velocity_scale": 0.12,
    "acceleration_scale": 0.12
  }
}
```

---

## 7. Locomotion — 3 levels of control

| Level | What it does | Obstacle avoidance? |
|---|---|---|
| **1 — Map Nav** | Give a destination + current pose; robot plans and follows a route. Implemented in the Supervisor. | Yes |
| **2 — Relative moves** | "Walk forward by X meters" style commands. Used for scripted behavior; available in the raw scripts (not exposed in the Supervisor UI). | Yes |
| **3 — Velocity commands** | Continuous forward/lateral/rotational speed sent to PC1. Used for custom planners/manual control. | **No** — robot becomes a 70kg "moving brick." Keep someone on safety watch. |

The robot **balances itself automatically** via a controller on PC1 — you virtually never manage balance directly. Keep it on the gantry during tests; remove only for field tests. If PC2 dies or stops sending commands mid-navigation, PC1 detects this and stops the robot until PC2 responds again.

---

## 8. Gestures / animations

- **133 built-in animations** by default. Comtrade has verified **20 as safe** (these are the ones exposed in the Supervisor's manual speech section). The other ~113 exist but are more dangerous (longer, sweeping motions) — be cautious using them.
- Can be played manually via the Supervisor's manual speech section, optionally paired with predefined speech.
- Custom animations can be added via **LinkCraft** or by recording your own joint movements.
- The robot does **not** stop itself from colliding with itself during animations — avoid motions that sweep across the chest or head/camera area.
- When idle, return joints to the **default neutral pose** — holding any other position for a long time overheats the joint motors.

---

## 9. Navigation & LiDAR mapping

### Preconditions for navigation to work
1. Robot must be **localized** (its pose/location/map must be known — ask the on-duty mentor to localize it if needed; it should already be localized at event start unless powered off since).
2. Robot must **not** be mid-animation or idling. Use the **"Arm"** feature (Navigation section) to put it in standby-for-movement mode (this disables idle animations but not navigation-triggered ones).
3. Nothing may be sitting in the robot's nav points — if there is, it stops and waits (there is no "please move" audio prompt implemented).

### How mapping works
- Preferred method: use the **tablet's "Map" function** (gives visual confirmation as you walk the robot around). Only use in-code mapping if you specifically need it for custom logic.
- Maps stored in **SQLite**: `/agibot/data/var/MapManagerModule/map.db`.
- Helper script exists: `/agibot/humanoid-platform/robot_services/autonomous_navigation/testing_controls/a2_map.py` — gives images/data of maps for easier development.

| File | Extension | What it is |
|---|---|---|
| occupancy_map | .png / .yaml | The actual map |
| map | .pcd | 3D coordinate measurements |
| keyframes | .pcd | — |
| frame_poses | .txt | (t, x, y, z, qx, qy, qz, qw) robot poses along its path |
| trajectories | .txt | Trajectories taken |
| grid_map_info | .txt | Special locations (virtual walls, nav points, etc.) |

**Important gotchas**:
- **Origin is TOP-LEFT with v pointing DOWN** — do NOT use ROS2's formula (it puts waypoints in the wrong place). Use `u = (x - ox) / res` and `v = (oy - y) / res`.
- `occupancy_map.png` uses 3 RGB colors, NOT the standard ROS2 occupancy image: `(224,229,241)` = free space, `(157,166,189)` = occupied, `(255,255,255)` = unknown.
- The `.yaml` version of the same map is **BGR**, not RGB.
- The `.yaml`'s `image:` field is an **absolute path** — do not copy the folder elsewhere and expect it to work.
- `resolution` means two different things: `map_info` = 20.0 px/m, `.yaml` = 0.05 m/px. **Trust the `.yaml`.**
- `map_version` is unreliable — order by `map_id` instead.

### Navigation methods (via `PncService`, PC2 gateway `:51056`)
```
POST http://127.0.0.1:51056/rpc/aimdk.protocol.PncService/<Method>
```
| Method | Inputs | Use |
|---|---|---|
| PlanningNaviToGoal | map_id, target_id, guide_line_id | Full planner to a saved nav point |
| PlanningNaviToPose2D* | SE2Pose | Planner to arbitrary coordinate |
| DirectNaviToRelative | SE2Pose | Straight to a relative pose |
| LinearNaviToGoal / ToPose2D* | — | Linear motion |
| MobileNaviToGoal / ToPose2D* | — | Linear motion variant |
| PreciseNaviToGoal | target_id, pose_offset | Fine docking |
| MoveForward | angle, distance | Open-loop |
| SpinTurn / SpinTurnAndMoveForward | angle, [distance] | Rotation |
| ActionCancel / Pause / Resume / GetState | task_id | Lifecycle |
| SetNaviParams | — | Runtime tuning, no restart needed |
| CalculateNaviTime / AutoExplore / NoMapFollow / RecordCurrentPose | — | Estimation / exploration |

*Note: the `Pose2D` variants exist in firmware but are absent from the SDK protos — likely an unfinished planned feature. There is no clean point-to-point-to-arbitrary-coordinate method in the normal SDK.*

`SE2Pose = {position: {x, y}, angle}` in meters and radians.

**Lifecycle**: `send task_id → poll ActionGetState → PncServiceState = IDLE / RUNNING / PAUSED / SUCCESS / FAILED`

### Navigation tuning config
File: `agibot/software/vectorflux_common/a2/app2.yaml`

| Parameter | Default | What it does |
|---|---|---|
| footprint | 0.672 m | Planner's robot-size model |
| spin_footprint | 0.70 m | Robot model during rotation |
| inflation_radius | 0.8 | Needs ≥1.6m gap to pass through a space |
| cost_scaling_factor | 5.5 | — |
| inflation_by_lateral_width | true | Adds half-width on top |
| stop_dist_normal | 1.2 m | Full stop distance from a person/obstacle |
| wait_change_path_delay_ms | 2000 | Waits 2s on current path before replanning |
| slowdown_dist_normal | 2.5 m | Distance at which it slows down |

- Config is **read only at startup**. To apply a change: `aima em reset-app pnc`, or `aima em stop-app pnc` then `start-app pnc`.
- `inflation_radius` appears **multiple times** in the yaml file for different parts of the nav stack — search with Ctrl+F and document which instance you changed.
- ⚠️ **Never edit the navigation config while the robot is standing on its own** — restarting `pnc` in certain standing modes can make it fall. Gantry first, always.

---

## 10. Known limitations / open issues (organizer team's own notes)

- **LiDAR blind spots**: worst near the shoulders and behind the backpack — small objects/children/animals near these spots may not be detected.
- **No collision prediction**: AimDK detects objects but doesn't predict/avoid intersecting paths on its own — this is an open item the organizer team flagged as worth solving.
- **Mapping needs ideal conditions**: moving objects during mapping can get baked into the map as permanent obstacles.
- **Navigation can get "confused"** in tight/narrow spaces (robot is wide — needs ≥1.6m of clearance by default).
- **Balance loss**: if bumped hard enough to disrupt low-level motor control, the robot may need a full E-Stop + reset to recover.
- **Glass**: LiDAR sees glass poorly. Comtrade added partial glass-detection safety features, but it's explicitly described as Beta/imperfect.

---

## 11. Safety essentials (must know before touching the robot)

- **E-Stop**: physical remote button, connects directly to the motor controller, bypasses all software. Held by a mentor. Forces immediate motor shutdown — robot collapses.
- **LiDAR-based stopping**: obstacle at ≤2.5m → robot slows and tries to reroute; at ≤1.2m → full stop, waits a few seconds before retrying. This is separate from the Supervisor's own extra safety layer.
- If something is too close, the robot **refuses new animations/non-locomotion moves** and holds position until it's clear.
- **Never** put fingers/hands/body parts between joints or inside the robot without a mentor present.
- **Never** hold a joint in one position for a long time when not needed — return to neutral pose (overheating risk).
- **Idle animation**: after ~10s of idleness the robot does a small idle-mimicry motion (rotating waist/head) which does **not** stop for nearby objects like other animations do — deactivate via the **"Arm"** button if this is a concern.
- **Activation**: press+release, then hold until the light turns green. Keep hands away from all motors — wrist/neck (and possibly arm) joints move during startup, and the LiDAR safety layer is **not active** during this sequence.
- **Deactivation** (safest procedure): put into hanging mode via tablet → lift with gantry until off the ground → get behind the robot → press-and-hold the button.
- **Battery swap**: robot can stay in the same session while swapping. Plug in the charger cable first (side lights turn green = charging), then swap the battery pack quickly (side lights: green = charging/OK, red = low/removed) — keep all body parts away from motors throughout.
- The robot weighs **~70kg** — treat it accordingly even though its movements can look harmless/clumsy.

---

## 12. Hackathon-specific context (A2 team)

- Teams working with the **A2 are assigned Category 4 as an A2-specific platform task** (distinct from the X2 teams' Category 4 task).
- A2 teams can also build for Categories 1–3 (conversation, gesture/voice commands, navigation A→B), since the A2 supports all of these.
- The A2's **functional arms** are its unique advantage for anything involving manipulation/grasping — lean into this for whichever category you pick.
- General event structure: ~8h working window with shifts (~2h each). Final deliverable is a ~20-minute presentation video of your working result, plus a short verbal explanation by a team representative.
- Existing infrastructure to build on rather than reinvent: the Supervisor (speech/vision/navigation), a LiveKit server, and a self-hosted Qdrant vector database on the robot for persona/RAG-style knowledge.
- A shared code repository and starting-template documentation exist from the organizers — use those as the actual starting point rather than starting from zero.
