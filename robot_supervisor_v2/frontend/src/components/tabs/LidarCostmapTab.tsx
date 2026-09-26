/**
 * LidarCostmapTab — live local costmap from the A2's MID-360 LiDAR.
 *
 * Data path:
 *   hal_lidar (always running)
 *     -> /aima/hal/lidar/neck/pointcloud   (ROS 2, ~10 Hz, ~20k points)
 *     -> a2_costmap.py sidecar             (transform to base_link, rasterise)
 *     -> GET /api/lidar/costmap/stream     (SSE, this component)
 *
 * The sidecar is started by the backend on the first subscriber and stopped a
 * few seconds after the last one leaves, so closing this tab costs nothing.
 *
 * View convention (matches how you'd stand behind the robot):
 *   up on screen    = +x = robot forward
 *   left on screen  = +y = robot left
 *
 * Colours are deliberately fixed rather than theme-derived: this is a sensor
 * readout, and a white ground keeps red/green legible in both light and dark UI.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { API_BASE } from '@/api/base';

const COLOR_BG = '#ffffff';
const COLOR_OBSTACLE = '#dc2626'; // red — anything the LiDAR sees
const COLOR_ROBOT = '#16a34a'; // green — the robot's own footprint
const COLOR_GLASS = '#0891b2'; // cyan — suspected glass (advisory only)
const COLOR_GRID = '#e5e7eb';
const COLOR_RING = '#cbd5e1';
const COLOR_AXIS = '#94a3b8';

/** A fitted planar segment through suspected-glass cells, metres in base_link. */
interface GlassLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  inliers: number;
  rms: number;
  length: number;
  density: number;
}

interface GlassStats {
  windows: number;
  windows_discarded: number;
  window_frames: number;
  frames_in_window: number;
  motion: number;
  settling: boolean;
}

interface CostmapFrame {
  type: string;
  seq?: number;
  hz?: number;
  radius: number;
  resolution: number;
  n: number;
  n_cells?: number;
  points_raw?: number;
  points_used?: number;
  decay?: number;
  footprint?: number[][];
  cells?: string;
  hits?: string;
  message?: string;
  n_glass?: number;
  glass_cells?: string;
  glass_conf?: string;
  glass_lines?: GlassLine[];
  glass_stats?: GlassStats;
}

/** Decoded obstacle cells: metres in base_link, plus 0..1 confidence. */
interface DecodedCells {
  x: Float32Array;
  y: Float32Array;
  conf: Float32Array;
  count: number;
}

/**
 * Unpack the sidecar's base64 payloads.
 *
 * `cells` is a Uint16 little-endian stream of interleaved [ix, iy] cell indices;
 * `hits` is one Uint8 confidence per cell. We read with an explicit DataView
 * rather than a Uint16Array view so the result does not depend on the browser's
 * platform endianness.
 */
const EMPTY_CELLS: DecodedCells = {
  x: new Float32Array(0),
  y: new Float32Array(0),
  conf: new Float32Array(0),
  count: 0,
};

function decodePacked(
  cellsB64: string | undefined,
  hitsB64: string | undefined,
  resolution: number,
  radius: number,
): DecodedCells {
  if (!cellsB64 || !hitsB64) return EMPTY_CELLS;

  const binary = atob(cellsB64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const view = new DataView(bytes.buffer);
  const count = Math.floor(binary.length / 4); // 2 x uint16 per cell

  const hitsBin = atob(hitsB64);

  const x = new Float32Array(count);
  const y = new Float32Array(count);
  const conf = new Float32Array(count);

  for (let i = 0; i < count; i += 1) {
    const ix = view.getUint16(i * 4, true);
    const iy = view.getUint16(i * 4 + 2, true);
    // cell index -> centre in metres (same rule both axes)
    x[i] = (ix + 0.5) * resolution - radius;
    y[i] = (iy + 0.5) * resolution - radius;
    conf[i] = (hitsBin.charCodeAt(i) || 0) / 255;
  }
  return { x, y, conf, count };
}

/** Obstacle cells. `glass_cells` uses the identical packing, so it reuses this. */
function decodeCells(frame: CostmapFrame): DecodedCells {
  return decodePacked(frame.cells, frame.hits, frame.resolution, frame.radius);
}

function decodeGlass(frame: CostmapFrame): DecodedCells {
  return decodePacked(frame.glass_cells, frame.glass_conf, frame.resolution, frame.radius);
}

export function LidarCostmapTab() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef<CostmapFrame | null>(null);
  const decodedRef = useRef<DecodedCells | null>(null);
  const glassRef = useRef<DecodedCells | null>(null);
  const rafRef = useRef<number | null>(null);

  const [radius, setRadius] = useState(10);
  const [resolution, setResolution] = useState(0.1);
  const [decay, setDecay] = useState(0.8);
  const [showGrid, setShowGrid] = useState(true);
  const [showGlass, setShowGlass] = useState(true);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState({ hz: 0, cells: 0, used: 0, raw: 0, seq: 0 });
  const [glass, setGlass] = useState<{ n: number; stats: GlassStats | null }>({
    n: 0,
    stats: null,
  });

  // ---------------------------------------------------------------- drawing
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const cssSize = canvas.clientWidth;
    if (cssSize === 0) return;
    if (canvas.width !== Math.round(cssSize * dpr)) {
      canvas.width = Math.round(cssSize * dpr);
      canvas.height = Math.round(cssSize * dpr);
    }
    const size = canvas.width;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = COLOR_BG;
    ctx.fillRect(0, 0, size, size);

    const frame = frameRef.current;
    const viewRadius = frame?.radius ?? radius;
    const cx = size / 2;
    const cy = size / 2;
    const scale = size / (2 * viewRadius); // pixels per metre

    // metres (base_link) -> canvas pixels. +x forward = up, +y left = left.
    const sx = (yM: number) => cx - yM * scale;
    const sy = (xM: number) => cy - xM * scale;

    // --- grid ---------------------------------------------------------
    if (showGrid) {
      ctx.strokeStyle = COLOR_GRID;
      ctx.lineWidth = 1;
      const step = viewRadius <= 4 ? 0.5 : viewRadius <= 12 ? 1 : 5;
      ctx.beginPath();
      for (let m = -Math.floor(viewRadius); m <= viewRadius; m += step) {
        ctx.moveTo(sx(m), 0);
        ctx.lineTo(sx(m), size);
        ctx.moveTo(0, sy(m));
        ctx.lineTo(size, sy(m));
      }
      ctx.stroke();
    }

    // --- range rings every 1/4 of the window --------------------------
    ctx.strokeStyle = COLOR_RING;
    ctx.lineWidth = 1;
    for (let i = 1; i <= 4; i += 1) {
      const r = (viewRadius * i) / 4;
      ctx.beginPath();
      ctx.arc(cx, cy, r * scale, 0, Math.PI * 2);
      ctx.stroke();
    }

    // --- axes ---------------------------------------------------------
    ctx.strokeStyle = COLOR_AXIS;
    ctx.beginPath();
    ctx.moveTo(cx, 0);
    ctx.lineTo(cx, size);
    ctx.moveTo(0, cy);
    ctx.lineTo(size, cy);
    ctx.stroke();

    // --- obstacle cells, as squares -----------------------------------
    const decoded = decodedRef.current;
    if (frame && decoded && decoded.count > 0) {
      const cellPx = Math.max(1, frame.resolution * scale);
      const half = cellPx / 2;
      ctx.fillStyle = COLOR_OBSTACLE;
      for (let i = 0; i < decoded.count; i += 1) {
        // Confidence modulates opacity: a cell seen once is faint, a wall is
        // solid. Batching by alpha would be faster but this is ~2k rects.
        ctx.globalAlpha = 0.35 + 0.65 * decoded.conf[i];
        ctx.fillRect(sx(decoded.y[i]) - half, sy(decoded.x[i]) - half, cellPx, cellPx);
      }
      ctx.globalAlpha = 1;
    }

    // --- suspected glass ----------------------------------------------
    // Drawn OVER the obstacle layer, because a pane usually also produces some
    // ordinary returns and we want the cyan to win where both apply.
    const glassCells = glassRef.current;
    if (showGlass && frame && glassCells && glassCells.count > 0) {
      const cellPx = Math.max(1, frame.resolution * scale);
      const half = cellPx / 2;
      ctx.fillStyle = COLOR_GLASS;
      for (let i = 0; i < glassCells.count; i += 1) {
        ctx.globalAlpha = 0.45 + 0.55 * glassCells.conf[i];
        ctx.fillRect(
          sx(glassCells.y[i]) - half,
          sy(glassCells.x[i]) - half,
          cellPx,
          cellPx,
        );
      }
      ctx.globalAlpha = 1;
    }

    // --- fitted glass planes -------------------------------------------
    // The line is the actual product here: a cell only survives to the frame at
    // all if it belongs to one of these, so drawing them makes the reasoning
    // visible rather than showing a cloud of cyan dots.
    const glassLines = showGlass ? frame?.glass_lines : undefined;
    if (glassLines && glassLines.length) {
      ctx.strokeStyle = COLOR_GLASS;
      ctx.lineWidth = 3;
      ctx.setLineDash([10, 6]);
      glassLines.forEach((ln) => {
        ctx.beginPath();
        ctx.moveTo(sx(ln.y1), sy(ln.x1));
        ctx.lineTo(sx(ln.y2), sy(ln.x2));
        ctx.stroke();
      });
      ctx.setLineDash([]);
    }

    // --- robot footprint ----------------------------------------------
    const footprint = frame?.footprint;
    if (footprint && footprint.length > 2) {
      ctx.beginPath();
      footprint.forEach(([fx, fy], i) => {
        const px = sx(fy);
        const py = sy(fx);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.closePath();
      ctx.fillStyle = COLOR_ROBOT;
      ctx.globalAlpha = 0.85;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.strokeStyle = COLOR_ROBOT;
      ctx.lineWidth = 2;
      ctx.stroke();

      // heading tick, so "forward" is unambiguous at a glance
      ctx.beginPath();
      ctx.moveTo(sx(0), sy(0.18));
      ctx.lineTo(sx(0), sy(0.18 + Math.min(0.6, viewRadius * 0.06)));
      ctx.strokeStyle = COLOR_ROBOT;
      ctx.lineWidth = 3;
      ctx.stroke();
    }

    // --- ring labels ---------------------------------------------------
    ctx.fillStyle = '#64748b';
    ctx.font = `${Math.round(11 * dpr)}px ui-monospace, monospace`;
    ctx.textAlign = 'left';
    for (let i = 1; i <= 4; i += 1) {
      const r = (viewRadius * i) / 4;
      ctx.fillText(`${r.toFixed(r < 10 ? 1 : 0)} m`, cx + 4, cy - r * scale + 12 * dpr);
    }
    ctx.textAlign = 'center';
    ctx.fillText('forward (+x)', cx, 14 * dpr);
  }, [radius, showGrid, showGlass]);

  // Redraw on a rAF loop so canvas work is decoupled from SSE arrival rate.
  useEffect(() => {
    const loop = () => {
      draw();
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [draw]);

  // ---------------------------------------------------------------- stream
  const streamUrl = useMemo(() => {
    const q = new URLSearchParams({
      radius: String(radius),
      resolution: String(resolution),
      decay: String(decay),
      // Detection runs in the sidecar; `showGlass` only hides the layer, so the
      // evidence keeps accumulating and toggling back on is instant rather than
      // restarting the sidecar and waiting to re-settle.
      glass: 'true',
    });
    return `${API_BASE}/api/lidar/costmap/stream?${q.toString()}`;
  }, [radius, resolution, decay]);

  useEffect(() => {
    if (paused) {
      setConnected(false);
      return;
    }
    // Opening the EventSource is what starts the sidecar; closing it is what
    // stops it. Changing any grid parameter tears this down and reconnects,
    // which restarts the sidecar with the new geometry.
    const source = new EventSource(streamUrl);
    let alive = true;

    source.onopen = () => {
      if (!alive) return;
      setConnected(true);
      setError(null);
    };

    source.onmessage = (event) => {
      if (!alive) return;
      let obj: CostmapFrame;
      try {
        obj = JSON.parse(event.data);
      } catch {
        return;
      }
      if (obj.type === 'error') {
        setError(obj.message ?? 'stream error');
        return;
      }
      if (obj.type === 'hello') {
        frameRef.current = { ...(frameRef.current ?? obj), ...obj };
        return;
      }
      frameRef.current = obj;
      decodedRef.current = decodeCells(obj);
      glassRef.current = decodeGlass(obj);
      setGlass({ n: obj.n_glass ?? 0, stats: obj.glass_stats ?? null });
      setStats({
        hz: obj.hz ?? 0,
        cells: obj.n_cells ?? 0,
        used: obj.points_used ?? 0,
        raw: obj.points_raw ?? 0,
        seq: obj.seq ?? 0,
      });
      setError(null);
    };

    source.onerror = () => {
      if (!alive) return;
      setConnected(false);
      // EventSource retries on its own; only surface a message.
      setError('stream disconnected — retrying');
    };

    return () => {
      alive = false;
      source.close();
      setConnected(false);
    };
  }, [streamUrl, paused]);

  // ---------------------------------------------------------------- render
  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">LiDAR local costmap</h2>
            <p className="text-sm text-muted-foreground">
              Live MID-360 returns rasterised into a robot-centred occupancy grid.
              Up is forward. Green is the robot&apos;s planner footprint, red squares are
              detected obstacles, and cyan marks suspected glass (advisory).
            </p>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${
                connected ? 'bg-emerald-500' : 'bg-slate-400'
              }`}
            />
            <span className="text-muted-foreground">
              {paused ? 'paused' : connected ? 'streaming' : 'connecting…'}
            </span>
            <button
              type="button"
              onClick={() => setPaused((p) => !p)}
              className="rounded-full border border-border px-3 py-1 text-sm font-medium transition hover:bg-muted"
            >
              {paused ? 'Resume' : 'Pause'}
            </button>
          </div>
        </div>

        {error && (
          <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
            {error}
          </div>
        )}

        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          <label className="text-sm">
            <span className="text-muted-foreground">
              Range: <strong className="text-foreground">{radius} m</strong>
            </span>
            <input
              type="range"
              min={2}
              max={30}
              step={1}
              value={radius}
              onChange={(e) => setRadius(Number(e.target.value))}
              className="mt-1 w-full"
            />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">
              Cell size: <strong className="text-foreground">{resolution.toFixed(2)} m</strong>
            </span>
            <input
              type="range"
              min={0.05}
              max={0.5}
              step={0.05}
              value={resolution}
              onChange={(e) => setResolution(Number(e.target.value))}
              className="mt-1 w-full"
            />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">
              Memory (decay): <strong className="text-foreground">{decay.toFixed(2)}</strong>
            </span>
            <input
              type="range"
              min={0}
              max={0.95}
              step={0.05}
              value={decay}
              onChange={(e) => setDecay(Number(e.target.value))}
              className="mt-1 w-full"
            />
          </label>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={showGrid}
              onChange={(e) => setShowGrid(e.target.checked)}
            />
            Show grid
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={showGlass}
              onChange={(e) => setShowGlass(e.target.checked)}
            />
            <span style={{ color: COLOR_GLASS }}>Suspected glass</span>
          </label>
          <span>
            {stats.hz.toFixed(1)} Hz · {stats.cells} cells · {stats.used}/{stats.raw} points ·
            frame {stats.seq}
          </span>
          <span className="text-amber-600 dark:text-amber-500">
            decay &gt; 0 remembers cells for ~{decay > 0 ? (1 / (1 - decay) / 10).toFixed(1) : '0'} s
            — set it to 0 while the robot is driving
          </span>
        </div>

        {showGlass && (
          <div className="mt-3 rounded-lg border border-cyan-600/30 bg-cyan-600/5 px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span className="font-medium" style={{ color: COLOR_GLASS }}>
                Glass: {glass.n} cell{glass.n === 1 ? '' : 's'}
                {glass.stats?.settling ? ' (settling…)' : ''}
              </span>
              {glass.stats && (
                <span className="text-muted-foreground">
                  {glass.stats.windows} window{glass.stats.windows === 1 ? '' : 's'} kept ·{' '}
                  {glass.stats.windows_discarded} discarded for motion
                </span>
              )}
              {glass.stats && glass.stats.motion > 0.35 && (
                <span className="text-amber-600 dark:text-amber-500">
                  scene is moving — detection paused
                </span>
              )}
            </div>
            <p className="mt-1 text-muted-foreground">
              <strong>Advisory only.</strong> Cells that keep appearing and disappearing, which
              is how glass reads to this LiDAR, and only where they form a dense straight run.
              This does not feed navigation and will not stop the robot — treat a dashed line as
              &ldquo;look here with your eyes&rdquo;. Needs the robot to be standing still.
            </p>
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="mx-auto w-full max-w-3xl">
          <canvas
            ref={canvasRef}
            className="aspect-square w-full rounded-xl border border-border"
            style={{ backgroundColor: COLOR_BG }}
          />
        </div>
      </div>
    </div>
  );
}

export default LidarCostmapTab;
