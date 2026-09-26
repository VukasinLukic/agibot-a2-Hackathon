/**
 * NavigationMissionsTab — plan a waypoint route on the map, say what to do at each
 * stop, run it, and watch the real planned path while the robot drives.
 *
 * Data path:
 *   map.db + occupancy_map.png  -> GET /api/nav/maps, /maps/{id}/meta, /maps/{id}/image
 *   /pnc/path_publish (ROS 2)   -> a2_nav_stream.py sidecar -> \
 *   TransFormService (HTTP-RPC) ->                              GET /api/nav/live/stream (SSE)
 *   PncService (HTTP-RPC)       -> server-side mission runner -> /
 *
 * COORDINATES — the one thing to get right
 * ----------------------------------------
 * The A2's occupancy grid has its origin at the TOP-LEFT pixel with v pointing DOWN,
 * which is NOT the ROS bottom-left convention:
 *     u = (x - origin_x) / resolution
 *     v = (origin_y - y) / resolution
 * Using the ROS formula puts every waypoint hundreds of pixels out. The backend sends
 * waypoints with both world and pixel coords, but the live path and pose arrive in
 * world metres, so worldToPx() below has to do the conversion — hence it lives in one
 * place and nothing else does the arithmetic inline.
 *
 * Colours are fixed rather than theme-derived, like the LiDAR tab: this is an
 * operational readout and the overlays must stay legible on a white/grey map in both
 * light and dark UI.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { API_BASE } from '@/api/base';
import { SystemClock } from '@/components/SystemClock';

const COLOR_WAYPOINT = '#2563eb';        // blue — waypoints stored in the map
const COLOR_WAYPOINT_SEL = '#f59e0b';    // amber — waypoints used by this mission
const COLOR_ROUTE = '#f59e0b';           // amber — the mission's intended order
const COLOR_PATH = '#16a34a';            // green — pnc's ACTUAL planned polyline
const COLOR_PATH_REPLAN = '#dc2626';     // red — path published while replanning
const COLOR_ROBOT = '#dc2626';           // red — live pose
const COLOR_TRAIL = '#94a3b8';           // grey — where the robot actually went
const COLOR_GLASS = '#0891b2';           // cyan — suspected glass (advisory only)

interface MapRow {
  map_id: string;
  name: string;
  index: number;
  exists: boolean;
  recorded: string | null;
  waypoint_count: number;
  is_current: boolean;
}

interface Grid {
  resolution: number;
  width: number;
  height: number;
  origin_x: number;
  origin_y: number;
}

interface Waypoint {
  id: number;
  name: string;
  x: number;
  y: number;
  theta: number;
  u: number;
  v: number;
  in_bounds: boolean;
}

interface MapMeta {
  map_id: string;
  name: string;
  grid: Grid;
  waypoints: Waypoint[];
}

type ActionType = 'dwell' | 'turn' | 'speak' | 'gesture' | 'script';

interface MissionAction {
  type: ActionType;
  seconds: number;
  radians: number;
  script: string;
  args: string[];
  text: string;
  gesture: string;
  /** Actions performed at the same time as this one. One level deep only. */
  concurrent?: MissionAction[];
}

/** One of the robot's ~133 factory presets, as offered by the tablet. */
interface MotionPreset {
  motion_id: number;
  name_en: string;
  name_zh: string;
  duration_s: number;
  /** The value to store in a gesture action: "motion:<id>". */
  ref: string;
  /** True when this preset is also one of the curated safe_only gestures. */
  curated: boolean;
}

const ACTION_LABELS: Record<ActionType, string> = {
  dwell: 'wait',
  turn: 'turn',
  speak: 'speak',
  gesture: 'gesture',
  script: 'script',
};

interface MissionStep {
  waypoint_id: number;
  label: string;
  actions: MissionAction[];
}

interface Mission {
  id: string;
  name: string;
  map_id: string;
  steps: MissionStep[];
  loop: boolean;
  glass_survey?: boolean;
  glass_survey_seconds?: number;
}

/** A glass plane found during a survey, in MAP metres (already transformed). */
interface GlassLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  length?: number;
  density?: number;
}

interface GlassReport {
  lines: GlassLine[];
  waypoints_surveyed: number;
  waypoints_incomplete: number;
}

interface Pose {
  x: number;
  y: number;
  yaw: number;
  stamp: number | null;
}

interface RunnerState {
  active?: boolean;
  scheduled?: boolean;
  scheduled_local?: string | null;
  mission_id?: string;
  mission_name?: string;
  step_index?: number;
  total_steps?: number;
  phase?: string;
  message?: string;
}

interface HoldState {
  held?: boolean;
  rearms?: number;
  suppressed?: boolean;
  engaged_for_s?: number;
  last_error?: string | null;
}

interface NavStatus {
  mc_action?: string;
  can_walk?: boolean;
  localization_running?: boolean | null;
  pnc_state?: string;
  pnc_info?: string;
  pose?: Pose | null;
  blockers?: string[];
  runner?: RunnerState;
  stream_running?: boolean;
  hold?: HoldState;
}

interface IdleMotion {
  neck_enabled?: boolean;
  player_status?: string;
  current_motion?: string | null;
}

const emptyAction = (type: ActionType): MissionAction => ({
  type,
  seconds: type === 'dwell' ? 5 : 0,
  radians: type === 'turn' ? Math.PI / 2 : 0,
  script: '',
  args: [],
  text: '',
  gesture: '',
  concurrent: [],
});

/**
 * The inputs specific to one action type. Shared by primary actions and by the
 * concurrent ones attached to them, so both rows stay in step automatically.
 */
function ActionFields({
  action,
  scripts,
  gestures,
  gestureDurations,
  motions,
  onChange,
}: {
  action: MissionAction;
  scripts: string[];
  gestures: string[];
  gestureDurations: Record<string, number>;
  motions: MotionPreset[];
  onChange: (patch: Partial<MissionAction>) => void;
}) {
  const input = 'rounded border bg-background px-1 py-0.5';
  switch (action.type) {
    case 'dwell':
      return (
        <>
          <input
            type="number"
            className={`w-16 ${input}`}
            value={action.seconds}
            onChange={(e) => onChange({ seconds: Number(e.target.value) })}
          />
          <span>s</span>
        </>
      );
    case 'turn':
      return (
        <>
          <input
            type="number"
            step="0.1"
            className={`w-16 ${input}`}
            value={action.radians}
            onChange={(e) => onChange({ radians: Number(e.target.value) })}
          />
          <span>rad (+ = left)</span>
        </>
      );
    case 'speak':
      return (
        <input
          type="text"
          placeholder="what should it say?"
          className={`min-w-0 flex-1 ${input}`}
          value={action.text}
          onChange={(e) => onChange({ text: e.target.value })}
        />
      );
    case 'gesture':
      return (
        <select
          className={`min-w-0 flex-1 ${input}`}
          value={action.gesture}
          onChange={(e) => onChange({ gesture: e.target.value })}
        >
          <option value="">pick a gesture…</option>
          {/* The curated set is what the conversational agent is also allowed to
              use, and is the only one vetted as safe_only. */}
          <optgroup label="Curated (vetted safe)">
            {gestures.map((g) => (
              <option key={g} value={g}>
                {g}
                {gestureDurations[g] ? ` (~${gestureDurations[g]}s)` : ''}
              </option>
            ))}
          </optgroup>
          {/* Everything else the tablet can play. Not safety-classified, and some
              run over a minute — the duration is shown for exactly that reason. */}
          <optgroup label="Full library (as on the tablet)">
            {motions
              .filter((m) => !m.curated)
              .map((m) => (
                <option key={m.ref} value={m.ref}>
                  {m.name_en || m.name_zh} (~{m.duration_s}s)
                </option>
              ))}
          </optgroup>
        </select>
      );
    case 'script':
      return (
        <select
          className={`min-w-0 flex-1 ${input}`}
          value={action.script}
          onChange={(e) => onChange({ script: e.target.value })}
        >
          <option value="">pick a script…</option>
          {scripts.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      );
    default:
      return null;
  }
}

export function NavigationMissionsTab() {
  const [maps, setMaps] = useState<MapRow[]>([]);
  const [mapId, setMapId] = useState<string>('');
  const [meta, setMeta] = useState<MapMeta | null>(null);
  const [mapImage, setMapImage] = useState<HTMLImageElement | null>(null);
  const [loadError, setLoadError] = useState<string>('');

  const [missions, setMissions] = useState<Mission[]>([]);
  const [draft, setDraft] = useState<Mission | null>(null);
  const [scripts, setScripts] = useState<string[]>([]);
  const [gestures, setGestures] = useState<string[]>([]);
  // How long each gesture actually blocks for — the mission waits for
  // motion_player to go idle, so the route will not walk on mid-gesture.
  const [gestureDurations, setGestureDurations] = useState<Record<string, number>>({});
  const [motions, setMotions] = useState<MotionPreset[]>([]);
  // Which action types may run alongside which. Served by the backend so the UI
  // and the validator can never disagree about what is legal.
  const [concurrency, setConcurrency] = useState<Record<string, string[]>>({});
  const [speechOk, setSpeechOk] = useState<boolean | null>(null);
  const [speechDetail, setSpeechDetail] = useState<string>('');

  const [status, setStatus] = useState<NavStatus>({});
  const [pose, setPose] = useState<Pose | null>(null);
  const [path, setPath] = useState<number[][]>([]);
  const [replanning, setReplanning] = useState(false);
  const [glass, setGlass] = useState<GlassReport | null>(null);
  const [avoidStatus, setAvoidStatus] = useState<string>('');
  const [runner, setRunner] = useState<RunnerState>({});
  const [idle, setIdle] = useState<IdleMotion>({});
  const [holdState, setHoldState] = useState<HoldState>({});
  // Scheduled start. `prepare` arms the robot shortly before the time so it is
  // not sitting in McAction_DEFAULT or mid idle-animation when the moment comes.
  const [scheduleOn, setScheduleOn] = useState(false);
  const [scheduleAt, setScheduleAt] = useState('');
  const [schedulePrepare, setSchedulePrepare] = useState(true);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string>('');

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  // Breadcrumb of real positions. Kept in a ref because it updates at pose rate
  // (2 Hz) and re-rendering React for each point would be wasteful — the canvas
  // redraw already happens on every pose.
  const trailRef = useRef<number[][]>([]);

  // ----------------------------------------------------------------- loading
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [mapsRes, missionsRes, scriptsRes, gesturesRes, typesRes] =
          await Promise.all([
            fetch(`${API_BASE}/api/nav/maps`),
            fetch(`${API_BASE}/api/nav/missions`),
            fetch(`${API_BASE}/api/nav/scripts`),
            fetch(`${API_BASE}/api/nav/gestures`),
            fetch(`${API_BASE}/api/nav/action-types`),
          ]);
        if (!alive) return;
        const mapsJson = await mapsRes.json();
        setMaps(mapsJson.maps ?? []);
        // Default to the map pnc is actually working on; that is the only map a
        // mission can run against without switching the robot's working map.
        const current = (mapsJson.maps ?? []).find((m: MapRow) => m.is_current);
        const pick = current ?? (mapsJson.maps ?? [])[0];
        if (pick) setMapId(pick.map_id);
        setMissions((await missionsRes.json()).missions ?? []);
        setScripts((await scriptsRes.json()).scripts ?? []);
        const gj = await gesturesRes.json();
        setGestures(gj.gestures ?? []);
        setGestureDurations(gj.durations_s ?? {});
        setMotions(gj.motions ?? []);
        setConcurrency((await typesRes.json()).concurrency ?? {});
        // Speech probe is slow when TTS is down (it has to time out), so it runs
        // after the rest rather than blocking the first paint.
        void fetch(`${API_BASE}/api/nav/speech/status`)
          .then((r) => r.json())
          .then((j) => {
            setSpeechOk(Boolean(j.available));
            setSpeechDetail(String(j.detail ?? ''));
          })
          .catch(() => setSpeechOk(false));
      } catch (err) {
        if (alive) setLoadError(String(err));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!mapId) return;
    let alive = true;
    setMeta(null);
    setMapImage(null);
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/nav/maps/${mapId}/meta`);
        if (!res.ok) throw new Error(`meta ${res.status}`);
        const json: MapMeta = await res.json();
        if (!alive) return;
        setMeta(json);
        const img = new Image();
        img.onload = () => {
          if (alive) setMapImage(img);
        };
        img.onerror = () => {
          if (alive) setLoadError('map image failed to load');
        };
        img.src = `${API_BASE}/api/nav/maps/${mapId}/image`;
      } catch (err) {
        if (alive) setLoadError(String(err));
      }
    })();
    return () => {
      alive = false;
    };
  }, [mapId]);

  // ------------------------------------------------------------ live stream
  useEffect(() => {
    const es = new EventSource(`${API_BASE}/api/nav/live/stream`);
    es.onmessage = (evt) => {
      let obj: Record<string, unknown>;
      try {
        obj = JSON.parse(evt.data);
      } catch {
        return;
      }
      switch (obj.type) {
        case 'pose': {
          const p = (obj.pose ?? null) as Pose | null;
          setPose(p);
          if (p) {
            const trail = trailRef.current;
            const last = trail[trail.length - 1];
            // Only record real movement, so standing still does not grow the trail.
            if (!last || Math.hypot(p.x - last[0], p.y - last[1]) > 0.05) {
              trail.push([p.x, p.y]);
              if (trail.length > 3000) trail.shift();
            }
          }
          break;
        }
        case 'path':
          setPath((obj.points ?? []) as number[][]);
          break;
        case 'avoid':
          setAvoidStatus(String(obj.status ?? ''));
          setReplanning(Boolean(obj.replanning));
          break;
        case 'state':
          setStatus((prev) => ({ ...prev, ...(obj as NavStatus) }));
          if (obj.runner) setRunner(obj.runner as RunnerState);
          if (obj.hold) setHoldState(obj.hold as HoldState);
          break;
        case 'hold':
          setHoldState(obj as HoldState);
          break;
        case 'mission':
          setRunner(obj as RunnerState);
          break;
        case 'glass':
          setGlass(obj as unknown as GlassReport);
          break;
        default:
          break;
      }
    };
    es.onerror = () => {
      // EventSource retries on its own; surfacing every blip would be noise.
    };
    return () => es.close();
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/nav/status`);
      const json: NavStatus = await res.json();
      setStatus(json);
      if (json.runner) setRunner(json.runner);
      if (json.hold) setHoldState(json.hold);
      if (json.pose) setPose(json.pose);
    } catch {
      /* the SSE stream is the primary source; this is just the initial fill */
    }
    try {
      // Survey results live in the runner, not the browser, so a reload mid-run
      // (or after one) still shows what was found rather than an empty map.
      const res = await fetch(`${API_BASE}/api/nav/glass`);
      const json: GlassReport = await res.json();
      if (json.waypoints_surveyed > 0) setGlass(json);
    } catch {
      /* optional overlay — absence is not an error worth surfacing */
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  const refreshIdle = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/nav/idle-motion`);
      if (res.ok) setIdle(await res.json());
    } catch {
      /* non-fatal: the toggle just shows as unknown */
    }
  }, []);

  useEffect(() => {
    void refreshIdle();
    // The player flips between IDLE and OPERATING roughly every 80 s, so a slow
    // poll is enough to keep the badge honest without hammering the MC.
    const t = window.setInterval(() => void refreshIdle(), 10000);
    return () => window.clearInterval(t);
  }, [refreshIdle]);

  const toggleIdle = async (next: boolean) => {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/nav/idle-motion`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next, include_body: true }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail ?? res.statusText);
      setIdle(json);
      setToast(
        next
          ? 'idle motion enabled — the robot will look around again every ~80s'
          : 'idle motion disabled — it will stay in its current action',
      );
    } catch (err) {
      setToast(`idle toggle failed: ${err}`);
    } finally {
      setBusy(false);
    }
  };

  // ------------------------------------------------------------- projection
  /** Canvas geometry: how the map image is fitted into the visible canvas. */
  const view = useMemo(() => {
    const cw = 900;
    const ch = 620;
    if (!meta) return { scale: 1, dx: 0, dy: 0, cw, ch };
    const scale = Math.min(cw / meta.grid.width, ch / meta.grid.height);
    return {
      scale,
      dx: (cw - meta.grid.width * scale) / 2,
      dy: (ch - meta.grid.height * scale) / 2,
      cw,
      ch,
    };
  }, [meta]);

  /** World metres -> canvas px. The top-left/v-down rule lives ONLY here. */
  const worldToCanvas = useCallback(
    (x: number, y: number): [number, number] => {
      if (!meta) return [0, 0];
      const u = (x - meta.grid.origin_x) / meta.grid.resolution;
      const v = (meta.grid.origin_y - y) / meta.grid.resolution;
      return [view.dx + u * view.scale, view.dy + v * view.scale];
    },
    [meta, view],
  );

  const pxToCanvas = useCallback(
    (u: number, v: number): [number, number] => [
      view.dx + u * view.scale,
      view.dy + v * view.scale,
    ],
    [view],
  );

  const canvasToPx = useCallback(
    (cx: number, cy: number): [number, number] => [
      (cx - view.dx) / view.scale,
      (cy - view.dy) / view.scale,
    ],
    [view],
  );

  // ----------------------------------------------------------------- drawing
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !meta) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== view.cw * dpr || canvas.height !== view.ch * dpr) {
      canvas.width = view.cw * dpr;
      canvas.height = view.ch * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, view.cw, view.ch);
    ctx.fillStyle = '#f1f5f9';
    ctx.fillRect(0, 0, view.cw, view.ch);

    if (mapImage) {
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(
        mapImage,
        view.dx,
        view.dy,
        meta.grid.width * view.scale,
        meta.grid.height * view.scale,
      );
    }

    // --- where the robot has actually been
    const trail = trailRef.current;
    if (trail.length > 1) {
      ctx.strokeStyle = COLOR_TRAIL;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      trail.forEach(([x, y], i) => {
        const [cx, cy] = worldToCanvas(x, y);
        if (i === 0) ctx.moveTo(cx, cy);
        else ctx.lineTo(cx, cy);
      });
      ctx.stroke();
    }

    // --- the mission's intended order (straight lines: this is the ROUTE, not a plan)
    const stepIds = draft?.steps.map((s) => s.waypoint_id) ?? [];
    if (stepIds.length > 1) {
      ctx.strokeStyle = COLOR_ROUTE;
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      stepIds.forEach((id, i) => {
        const w = meta.waypoints.find((p) => p.id === id);
        if (!w) return;
        const [cx, cy] = pxToCanvas(w.u, w.v);
        if (i === 0) ctx.moveTo(cx, cy);
        else ctx.lineTo(cx, cy);
      });
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // --- pnc's ACTUAL planned polyline, straight from /pnc/path_publish
    if (path.length > 1) {
      ctx.strokeStyle = replanning ? COLOR_PATH_REPLAN : COLOR_PATH;
      ctx.lineWidth = 3;
      ctx.beginPath();
      path.forEach(([x, y], i) => {
        const [cx, cy] = worldToCanvas(x, y);
        if (i === 0) ctx.moveTo(cx, cy);
        else ctx.lineTo(cx, cy);
      });
      ctx.stroke();
    }

    // --- suspected glass planes found during a survey
    // Drawn under the waypoints so it never hides a marker you need to click.
    if (glass && glass.lines.length) {
      ctx.strokeStyle = COLOR_GLASS;
      ctx.lineWidth = 4;
      ctx.setLineDash([9, 5]);
      glass.lines.forEach((g) => {
        const [ax, ay] = worldToCanvas(g.x1, g.y1);
        const [bx, by] = worldToCanvas(g.x2, g.y2);
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.stroke();
      });
      ctx.setLineDash([]);
    }

    // --- waypoints
    meta.waypoints.forEach((w) => {
      const [cx, cy] = pxToCanvas(w.u, w.v);
      const order = stepIds.indexOf(w.id);
      const selected = order >= 0;
      ctx.fillStyle = selected ? COLOR_WAYPOINT_SEL : COLOR_WAYPOINT;
      ctx.beginPath();
      ctx.arc(cx, cy, selected ? 8 : 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();
      // heading tick, so a waypoint's saved orientation is visible
      const hx = cx + Math.cos(-w.theta) * 16;
      const hy = cy + Math.sin(-w.theta) * 16;
      ctx.strokeStyle = selected ? COLOR_WAYPOINT_SEL : COLOR_WAYPOINT;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(hx, hy);
      ctx.stroke();

      ctx.font = '11px ui-sans-serif, system-ui, sans-serif';
      ctx.fillStyle = '#0f172a';
      ctx.strokeStyle = 'rgba(255,255,255,0.9)';
      ctx.lineWidth = 3;
      const label = selected ? `${order + 1}. ${w.name}` : w.name;
      ctx.strokeText(label, cx + 11, cy - 9);
      ctx.fillText(label, cx + 11, cy - 9);
    });

    // --- live pose
    if (pose) {
      const [cx, cy] = worldToCanvas(pose.x, pose.y);
      ctx.fillStyle = COLOR_ROBOT;
      ctx.beginPath();
      ctx.arc(cx, cy, 7, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();
      // v points DOWN, so a world yaw of +theta draws at -theta on screen.
      ctx.strokeStyle = COLOR_ROBOT;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.cos(-pose.yaw) * 22, cy + Math.sin(-pose.yaw) * 22);
      ctx.stroke();
    }
  }, [meta, mapImage, view, worldToCanvas, pxToCanvas, draft, path, pose, replanning,
      glass]);

  // ------------------------------------------------------------ interaction
  const handleCanvasClick = useCallback(
    async (evt: React.MouseEvent<HTMLCanvasElement>) => {
      if (!meta) return;
      const rect = evt.currentTarget.getBoundingClientRect();
      const cx = ((evt.clientX - rect.left) / rect.width) * view.cw;
      const cy = ((evt.clientY - rect.top) / rect.height) * view.ch;

      // Nearest waypoint within a generous radius -> add it as a step.
      let best: Waypoint | null = null;
      let bestDist = Infinity;
      meta.waypoints.forEach((w) => {
        const [wx, wy] = pxToCanvas(w.u, w.v);
        const d = Math.hypot(wx - cx, wy - cy);
        if (d < bestDist) {
          bestDist = d;
          best = w;
        }
      });

      if (evt.shiftKey) {
        // Shift-click empty space = record a NEW waypoint on the robot's map.
        const [u, v] = canvasToPx(cx, cy);
        const name = window.prompt('Name for the new waypoint?');
        if (!name) return;
        setBusy(true);
        try {
          const res = await fetch(`${API_BASE}/api/nav/maps/${mapId}/waypoints`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ u: Math.round(u), v: Math.round(v), name, theta: 0 }),
          });
          if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
          const metaRes = await fetch(`${API_BASE}/api/nav/maps/${mapId}/meta`);
          setMeta(await metaRes.json());
          setToast(`added waypoint "${name}"`);
        } catch (err) {
          setToast(`could not add waypoint: ${err}`);
        } finally {
          setBusy(false);
        }
        return;
      }

      if (best && bestDist < 20) {
        const w = best as Waypoint;
        setDraft((prev) => {
          const base: Mission =
            prev ?? { id: '', name: 'New mission', map_id: mapId, steps: [], loop: false, glass_survey: false, glass_survey_seconds: 20 };
          return {
            ...base,
            map_id: mapId,
            steps: [...base.steps, { waypoint_id: w.id, label: w.name, actions: [] }],
          };
        });
      }
    },
    [meta, view, pxToCanvas, canvasToPx, mapId],
  );

  // --------------------------------------------------------------- mutations
  const updateStep = (i: number, patch: Partial<MissionStep>) =>
    setDraft((prev) =>
      prev
        ? { ...prev, steps: prev.steps.map((s, j) => (j === i ? { ...s, ...patch } : s)) }
        : prev,
    );

  const removeStep = (i: number) =>
    setDraft((prev) =>
      prev ? { ...prev, steps: prev.steps.filter((_, j) => j !== i) } : prev,
    );

  const moveStep = (i: number, delta: number) =>
    setDraft((prev) => {
      if (!prev) return prev;
      const j = i + delta;
      if (j < 0 || j >= prev.steps.length) return prev;
      const steps = [...prev.steps];
      [steps[i], steps[j]] = [steps[j], steps[i]];
      return { ...prev, steps };
    });

  const saveDraft = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/nav/missions`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
      const saved: Mission = await res.json();
      setDraft(saved);
      const list = await (await fetch(`${API_BASE}/api/nav/missions`)).json();
      setMissions(list.missions ?? []);
      setToast(`saved "${saved.name}"`);
    } catch (err) {
      setToast(`save failed: ${err}`);
    } finally {
      setBusy(false);
    }
  };

  const runDraft = async () => {
    if (!draft?.id) {
      setToast('save the mission before running it');
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/nav/missions/${draft.id}/run`, {
        method: 'POST',
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail ?? res.statusText);
      setRunner(json);
      trailRef.current = [];
      setToast('mission started');
    } catch (err) {
      setToast(String(err));
    } finally {
      setBusy(false);
    }
  };

  const scheduleDraft = async () => {
    if (!draft?.id) {
      setToast('save the mission before scheduling it');
      return;
    }
    if (!scheduleAt) {
      setToast('pick a start time first');
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/nav/missions/${draft.id}/schedule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ at: scheduleAt, prepare: schedulePrepare }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail ?? res.statusText);
      setRunner(json);
      trailRef.current = [];
      setToast(`armed for ${json.scheduled_local ?? scheduleAt}`);
    } catch (err) {
      setToast(String(err));
    } finally {
      setBusy(false);
    }
  };

  const unschedule = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/nav/run/unschedule`, { method: 'POST' });
      setRunner(await res.json());
      setToast('schedule cancelled');
    } catch (err) {
      setToast(String(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleArm = async (arm: boolean) => {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/nav/${arm ? 'arm' : 'disarm'}`, {
        method: 'POST',
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail ?? res.statusText);
      setHoldState(json);
      setToast(
        arm
          ? `armed — holding a walking action (${json.action ?? 'ok'})`
          : `disarmed — ${json.idle_motion ?? 'released'}`,
      );
      await refreshStatus();
    } catch (err) {
      setToast(String(err));
    } finally {
      setBusy(false);
    }
  };

  const cancelRun = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/nav/run/cancel`, { method: 'POST' });
      setRunner(await res.json());
      setToast('cancelled');
    } catch (err) {
      setToast(String(err));
    } finally {
      setBusy(false);
    }
  };

  const deleteMission = async (id: string) => {
    if (!window.confirm('Delete this mission?')) return;
    await fetch(`${API_BASE}/api/nav/missions/${id}`, { method: 'DELETE' });
    const list = await (await fetch(`${API_BASE}/api/nav/missions`)).json();
    setMissions(list.missions ?? []);
    if (draft?.id === id) setDraft(null);
  };

  const blockers = status.blockers ?? [];
  const canRun = blockers.length === 0 && !runner.active;

  // ------------------------------------------------------------------ render
  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold">Navigation missions</h2>
          <select
            className="rounded border bg-background px-2 py-1 text-sm"
            value={mapId}
            onChange={(e) => {
              setMapId(e.target.value);
              setDraft(null);
            }}
          >
            {maps.map((m) => (
              <option key={m.map_id} value={m.map_id}>
                {m.name} ({m.waypoint_count} wp){m.is_current ? ' — current' : ''}
              </option>
            ))}
          </select>
          <span className="text-xs text-muted-foreground">
            {status.mc_action ?? '—'} · pnc {status.pnc_state ?? '—'} ·{' '}
            {pose
              ? `pose ${pose.x.toFixed(2)}, ${pose.y.toFixed(2)} @ ${((pose.yaw * 180) / Math.PI).toFixed(0)}°`
              : 'no pose'}
            {avoidStatus ? ` · ${avoidStatus}` : ''}
          </span>

          {/* Idle "liveliness" animation. skillpilot starts one about every 80 s,
              which drags the MC into RL_WHOLE_BODY_EXT_JOINT_SERVO and out of a
              locomotion action. Turning it off is a runtime RPC — nothing is
              powered down and no service restarts. */}
          <span className="ml-auto">
            <SystemClock compact />
          </span>
          <label
            className="flex items-center gap-2 text-xs"
            title="skillpilot plays 灵动环顾 (look-around) roughly every 80s, which pulls the MC into RL_WHOLE_BODY_EXT_JOINT_SERVO. Not persisted: a reboot re-enables it."
          >
            <input
              type="checkbox"
              disabled={busy || idle.neck_enabled === undefined}
              checked={Boolean(idle.neck_enabled)}
              onChange={(e) => void toggleIdle(e.target.checked)}
            />
            idle animation
            {idle.player_status ? (
              <span className="text-muted-foreground">
                ({idle.player_status.replace('MotionCommandStatus_', '').toLowerCase()}
                {idle.current_motion ? ` · ${idle.current_motion}` : ''})
              </span>
            ) : null}
          </label>
        </div>

        {!maps.some((m) => m.map_id === mapId && m.is_current) && mapId ? (
          <p className="mt-2 rounded bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
            This is not the robot&apos;s current working map. Waypoint navigation
            resolves against the working map, so a mission on this map will fail with
            <code className="mx-1">get_map_fail</code> until the working map is
            switched.
          </p>
        ) : null}

        {blockers.length > 0 ? (
          <ul className="mt-2 space-y-1">
            {blockers.map((b) => (
              <li
                key={b}
                className="rounded bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-400"
              >
                {b}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 rounded bg-emerald-500/10 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-400">
            Ready: the legs can step and localization is running.
            {pose ? ' Live pose is being received.' : ''}
          </p>
        )}

        {/* TTSService lives in the `agent` app, which the supervisor's audio bridge
            can stop to take the audio devices. Warn up front rather than letting a
            mission fail at the first speak action. */}
        {holdState.held ? (
          <p className="mt-2 rounded bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
            <strong>Armed</strong> — holding a walking action for{' '}
            {Math.round(holdState.engaged_for_s ?? 0)}s. Legs are powered and the idle
            animation is off.
            {holdState.rearms
              ? ` Re-armed ${holdState.rearms}x (skillpilot keeps taking the action back).`
              : ''}
            {holdState.suppressed ? ' Paused while a gesture plays.' : ''}
            {holdState.last_error ? ` Last error: ${holdState.last_error}` : ''}
          </p>
        ) : null}

        {speechOk === false ? (
          <p className="mt-2 rounded bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
            Speech unavailable — <code>speak</code> actions will fail.{' '}
            {speechDetail}
          </p>
        ) : null}

        {!pose && status.stream_running === false ? (
          <p className="mt-2 rounded bg-slate-500/10 px-3 py-2 text-xs text-muted-foreground">
            Waiting for the live stream to start — pose arrives over it, not over a
            polled RPC.
          </p>
        ) : null}

        {runner.scheduled && runner.scheduled_local ? (
          <p className="mt-2 rounded bg-sky-500/10 px-3 py-2 text-xs text-sky-700 dark:text-sky-400">
            Armed for <strong>{runner.scheduled_local}</strong> (robot local time).{' '}
            {runner.message}
          </p>
        ) : null}
        {runner.active || runner.phase ? (
          <p className="mt-2 text-xs text-muted-foreground">
            Runner: <strong>{runner.phase}</strong>
            {runner.total_steps
              ? ` — step ${(runner.step_index ?? 0) + 1}/${runner.total_steps}`
              : ''}
            {runner.message ? ` — ${runner.message}` : ''}
          </p>
        ) : null}
        {glass && glass.waypoints_surveyed > 0 ? (
          <p className="mt-2 text-xs">
            <span style={{ color: COLOR_GLASS }} className="font-medium">
              Glass survey:
            </span>{' '}
            {glass.lines.length} plane{glass.lines.length === 1 ? '' : 's'} across{' '}
            {glass.waypoints_surveyed} stop
            {glass.waypoints_surveyed === 1 ? '' : 's'}
            {glass.waypoints_incomplete > 0 ? (
              // Never let this read as an all-clear: a stop where the detector
              // could not get a clean look tells you nothing about that spot.
              <span className="text-amber-600 dark:text-amber-500">
                {' '}
                — {glass.waypoints_incomplete} stop
                {glass.waypoints_incomplete === 1 ? '' : 's'} never got a clean look,
                so nothing is known there
              </span>
            ) : null}
          </p>
        ) : null}
        {loadError ? (
          <p className="mt-2 text-xs text-red-600">{loadError}</p>
        ) : null}
        {toast ? <p className="mt-2 text-xs text-muted-foreground">{toast}</p> : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="rounded-lg border bg-card p-3" ref={wrapRef}>
          <canvas
            ref={canvasRef}
            onClick={handleCanvasClick}
            style={{ width: '100%', height: 'auto', aspectRatio: `${view.cw} / ${view.ch}` }}
            className="cursor-crosshair rounded"
          />
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
            <span>Click a waypoint to append it · Shift-click to record a new one</span>
            <span className="flex items-center gap-1">
              <i className="inline-block h-2 w-4" style={{ background: COLOR_ROUTE }} />
              mission order
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block h-2 w-4" style={{ background: COLOR_PATH }} />
              pnc planned path
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block h-2 w-4" style={{ background: COLOR_TRAIL }} />
              actual trail
            </span>
            {replanning ? (
              <span style={{ color: COLOR_PATH_REPLAN }}>replanning around an obstacle</span>
            ) : null}
          </div>
        </div>

        <div className="space-y-3">
          <SystemClock />

          <div className="rounded-lg border bg-card p-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">Saved missions</h3>
              <button
                type="button"
                className="rounded border px-2 py-1 text-xs"
                onClick={() =>
                  setDraft({ id: '', name: 'New mission', map_id: mapId, steps: [], loop: false, glass_survey: false, glass_survey_seconds: 20 })
                }
              >
                New
              </button>
            </div>
            <ul className="mt-2 space-y-1">
              {missions.length === 0 ? (
                <li className="text-xs text-muted-foreground">none yet</li>
              ) : null}
              {missions.map((m) => (
                <li key={m.id} className="flex items-center justify-between gap-2 text-xs">
                  <button
                    type="button"
                    className="truncate text-left hover:underline"
                    onClick={() => setDraft(m)}
                  >
                    {m.name}{' '}
                    <span className="text-muted-foreground">({m.steps.length} steps)</span>
                  </button>
                  <button
                    type="button"
                    className="text-red-600 hover:underline"
                    onClick={() => void deleteMission(m.id)}
                  >
                    delete
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {draft ? (
            <div className="rounded-lg border bg-card p-3">
              <input
                className="w-full rounded border bg-background px-2 py-1 text-sm"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
              <label className="mt-2 flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={draft.loop}
                  onChange={(e) => setDraft({ ...draft, loop: e.target.checked })}
                />
                loop until cancelled
              </label>

              <label className="mt-2 flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={Boolean(draft.glass_survey)}
                  onChange={(e) => setDraft({ ...draft, glass_survey: e.target.checked })}
                />
                <span style={{ color: COLOR_GLASS }}>survey for glass at each stop</span>
              </label>
              {draft.glass_survey && (
                <div className="mt-2 rounded border border-cyan-600/30 bg-cyan-600/5 p-2 text-xs">
                  <label className="flex items-center gap-2">
                    stand still for
                    <input
                      type="number"
                      min={12}
                      max={120}
                      step={1}
                      value={draft.glass_survey_seconds ?? 20}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          glass_survey_seconds: Math.max(12, Number(e.target.value)),
                        })
                      }
                      className="w-16 rounded border bg-background px-1 py-0.5"
                    />
                    s per waypoint
                  </label>
                  <p className="mt-1 text-muted-foreground">
                    Detection only works while the robot is <strong>stationary</strong> —
                    driving slowly does not help, it finds nothing at all from about
                    0.05&nbsp;m/s. Each stop also needs time to settle first, so budget
                    roughly {(draft.glass_survey_seconds ?? 20) + 20}s per waypoint.
                  </p>
                  <p className="mt-1 text-amber-600 dark:text-amber-500">
                    <strong>Advisory.</strong> Findings are drawn on the map. They do not
                    change the route, are not given to the navigation stack, and will not
                    stop the robot hitting anything.
                  </p>
                </div>
              )}

              <ol className="mt-3 space-y-2">
                {draft.steps.map((step, i) => {
                  const w = meta?.waypoints.find((p) => p.id === step.waypoint_id);
                  return (
                    <li key={`${step.waypoint_id}-${i}`} className="rounded border p-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium">
                          {i + 1}. {w?.name ?? `waypoint ${step.waypoint_id}`}
                        </span>
                        <span className="flex gap-1 text-xs">
                          <button type="button" onClick={() => moveStep(i, -1)}>
                            ↑
                          </button>
                          <button type="button" onClick={() => moveStep(i, 1)}>
                            ↓
                          </button>
                          <button
                            type="button"
                            className="text-red-600"
                            onClick={() => removeStep(i)}
                          >
                            ✕
                          </button>
                        </span>
                      </div>

                      {step.actions.map((a, ai) => {
                        const patchAction = (patch: Partial<MissionAction>) =>
                          updateStep(i, {
                            actions: step.actions.map((x, j) =>
                              j === ai ? { ...x, ...patch } : x,
                            ),
                          });
                        const peers = a.concurrent ?? [];
                        // Only offer types the backend says can share this action,
                        // minus any already in the group (one voice, one body).
                        const taken = new Set([a.type, ...peers.map((p) => p.type)]);
                        const addable = (concurrency[a.type] ?? []).filter(
                          (c) => !taken.has(c as ActionType),
                        );
                        return (
                          <div key={ai} className="mt-1 rounded border border-dashed p-1">
                            <div className="flex items-center gap-1 text-xs">
                              <select
                                className="rounded border bg-background px-1 py-0.5"
                                value={a.type}
                                onChange={(e) =>
                                  updateStep(i, {
                                    actions: step.actions.map((x, j) =>
                                      j === ai
                                        ? emptyAction(e.target.value as ActionType)
                                        : x,
                                    ),
                                  })
                                }
                              >
                                {(Object.keys(ACTION_LABELS) as ActionType[]).map((k) => (
                                  <option key={k} value={k}>
                                    {ACTION_LABELS[k]}
                                  </option>
                                ))}
                              </select>
                              <ActionFields
                                action={a}
                                scripts={scripts}
                                gestures={gestures}
                                gestureDurations={gestureDurations}
                                motions={motions}
                                onChange={patchAction}
                              />
                              <button
                                type="button"
                                className="text-red-600"
                                title="delete this action"
                                onClick={() =>
                                  updateStep(i, {
                                    actions: step.actions.filter((_, j) => j !== ai),
                                  })
                                }
                              >
                                ✕
                              </button>
                            </div>

                            {peers.map((pa, pi) => (
                              <div
                                key={pi}
                                className="mt-1 flex items-center gap-1 pl-4 text-xs"
                              >
                                <span className="text-muted-foreground">+ at the same time</span>
                                <select
                                  className="rounded border bg-background px-1 py-0.5"
                                  value={pa.type}
                                  onChange={(e) =>
                                    patchAction({
                                      concurrent: peers.map((x, j) =>
                                        j === pi
                                          ? emptyAction(e.target.value as ActionType)
                                          : x,
                                      ),
                                    })
                                  }
                                >
                                  {/* the current type, plus anything still legal here */}
                                  {[pa.type, ...addable].map((k) => (
                                    <option key={k} value={k}>
                                      {ACTION_LABELS[k as ActionType]}
                                    </option>
                                  ))}
                                </select>
                                <ActionFields
                                  action={pa}
                                  scripts={scripts}
                                  gestures={gestures}
                                  gestureDurations={gestureDurations}
                                  motions={motions}
                                  onChange={(patch) =>
                                    patchAction({
                                      concurrent: peers.map((x, j) =>
                                        j === pi ? { ...x, ...patch } : x,
                                      ),
                                    })
                                  }
                                />
                                <button
                                  type="button"
                                  className="text-red-600"
                                  title="remove this concurrent action"
                                  onClick={() =>
                                    patchAction({
                                      concurrent: peers.filter((_, j) => j !== pi),
                                    })
                                  }
                                >
                                  ✕
                                </button>
                              </div>
                            ))}

                            {addable.length > 0 ? (
                              <button
                                type="button"
                                className="mt-1 ml-4 rounded border px-2 py-0.5 text-xs text-muted-foreground"
                                title={`Runs at the same time as this ${ACTION_LABELS[a.type]}. The group ends when the slowest part finishes.`}
                                onClick={() =>
                                  patchAction({
                                    concurrent: [
                                      ...peers,
                                      emptyAction(addable[0] as ActionType),
                                    ],
                                  })
                                }
                              >
                                + concurrent
                              </button>
                            ) : (
                              <p className="mt-1 ml-4 text-[11px] text-muted-foreground">
                                {(concurrency[a.type] ?? []).length === 0
                                  ? `nothing can run alongside a ${ACTION_LABELS[a.type]}`
                                  : 'all compatible actions already added'}
                              </p>
                            )}
                          </div>
                        );
                      })}

                      <button
                        type="button"
                        className="mt-1 rounded border px-2 py-0.5 text-xs"
                        onClick={() =>
                          updateStep(i, { actions: [...step.actions, emptyAction('dwell')] })
                        }
                      >
                        + action on arrival
                      </button>
                    </li>
                  );
                })}
              </ol>
              {draft.steps.length === 0 ? (
                <p className="mt-2 text-xs text-muted-foreground">
                  Click waypoints on the map to build the route.
                </p>
              ) : null}

              {/* Scheduled start. The time is the ROBOT's local wall clock, which
                  has no NTP — the clock panel above says so. */}
              <div className="mt-3 rounded border p-2">
                <label className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={scheduleOn}
                    onChange={(e) => setScheduleOn(e.target.checked)}
                  />
                  start at a specific time
                </label>
                {scheduleOn ? (
                  <div className="mt-2 space-y-2">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <input
                        type="time"
                        step={1}
                        className="rounded border bg-background px-2 py-1"
                        value={scheduleAt}
                        onChange={(e) => setScheduleAt(e.target.value)}
                      />
                      <span className="text-muted-foreground">
                        24h, robot local time; seconds optional. A time already past
                        today means tomorrow.
                      </span>
                    </div>
                    <label className="flex items-start gap-2 text-xs">
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={schedulePrepare}
                        onChange={(e) => setSchedulePrepare(e.target.checked)}
                      />
                      <span>
                        get the robot ready 60s before (stops the idle animation and
                        puts the legs into a walking action).{' '}
                        <strong>This powers the leg motors.</strong>
                      </span>
                    </label>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={busy || runner.active}
                        onClick={() => void scheduleDraft()}
                        className="rounded bg-sky-600 px-3 py-1 text-xs text-white disabled:opacity-50"
                      >
                        Arm
                      </button>
                      <button
                        type="button"
                        disabled={busy || !runner.scheduled}
                        onClick={() => void unschedule()}
                        className="rounded border px-3 py-1 text-xs disabled:opacity-50"
                      >
                        Disarm
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy}
                  title={
                    holdState.held
                      ? 'Stop holding and give the robot back to AgiBot idle behaviour.'
                      : 'Hold the robot in a walking action until disarmed. A one-shot arm is stolen back by skillpilot within ~60s. Powers the leg motors.'
                  }
                  onClick={() => void toggleArm(!holdState.held)}
                  className={`rounded px-3 py-1 text-xs text-white disabled:opacity-50 ${
                    holdState.held ? 'bg-amber-600' : 'bg-slate-700'
                  }`}
                >
                  {holdState.held ? 'DISARM' : 'ARM ROBOT'}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void saveDraft()}
                  className="rounded bg-slate-700 px-3 py-1 text-xs text-white disabled:opacity-50"
                >
                  Save
                </button>
                <button
                  type="button"
                  disabled={busy || !canRun}
                  onClick={() => void runDraft()}
                  title={blockers[0] ?? ''}
                  className="rounded bg-emerald-600 px-3 py-1 text-xs text-white disabled:opacity-50"
                >
                  Run
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void cancelRun()}
                  className="rounded bg-red-600 px-3 py-1 text-xs text-white disabled:opacity-50"
                >
                  Cancel / Stop
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
