import { useEffect, useState } from 'react';
import { RefreshCw, Trophy, Users } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type { IgraLeaderboardResponse, IgraStatusResponse } from '@/api/types';

function formatTs(value?: number | null) {
  if (!value) return '—';
  try {
    return new Date(value * 1000).toLocaleString();
  } catch {
    return '—';
  }
}

export function LeaderboardTab() {
  const [board, setBoard] = useState<IgraLeaderboardResponse | null>(null);
  const [status, setStatus] = useState<IgraStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [leaderboard, igraStatus] = await Promise.all([
        api.getIgraLeaderboard(),
        api.getIgraStatus().catch(() => null),
      ]);
      setBoard(leaderboard);
      setStatus(igraStatus);
    } catch (error) {
      toast.error(`Failed to load IGRA leaderboard: ${error}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(id);
  }, []);

  const players = board?.players ?? [];
  const detect = status?.status;

  return (
    <section className="space-y-4">
      <section className="rounded-xl border border-border/60 bg-card p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">IGRA Leaderboard</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Current players for papir / kamen / makaze. Empty until matches are recorded.
            </p>
            <p className="mt-2 text-[11px] text-muted-foreground">
              Service: {status?.service_state ?? board?.service_state ?? 'unknown'}
              {detect?.last_stable_label
                ? ` · last gesture: ${detect.last_stable_label}`
                : ''}
              {detect?.announce_text ? ` · announce: “${detect.announce_text}”` : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-md border border-border bg-muted px-3 py-2 text-xs font-semibold disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : undefined} /> Refresh
          </button>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <div className="rounded-lg border border-border/60 bg-muted/30 p-3">
            <p className="text-[11px] uppercase text-muted-foreground">Players</p>
            <p className="mt-1 text-2xl font-semibold">{board?.player_count ?? 0}</p>
          </div>
          <div className="rounded-lg border border-border/60 bg-muted/30 p-3">
            <p className="text-[11px] uppercase text-muted-foreground">Camera</p>
            <p className="mt-1 text-sm font-semibold">
              {detect?.camera_ok ? 'OK' : detect ? 'Down / idle' : 'IGRA offline'}
            </p>
          </div>
          <div className="rounded-lg border border-border/60 bg-muted/30 p-3">
            <p className="text-[11px] uppercase text-muted-foreground">Labels</p>
            <p className="mt-1 truncate text-sm font-semibold">
              {(detect?.labels ?? []).join(', ') || '—'}
            </p>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-border/60 bg-card p-5 shadow-sm">
        <div className="mb-3 flex items-center gap-2">
          <Users size={16} className="text-rose-400" />
          <h3 className="text-sm font-semibold">Current players</h3>
        </div>
        {players.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
            No players yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-[11px] uppercase text-muted-foreground">
                <tr>
                  <th className="px-2 py-2">#</th>
                  <th className="px-2 py-2">Player</th>
                  <th className="px-2 py-2">Wins</th>
                  <th className="px-2 py-2">Losses</th>
                  <th className="px-2 py-2">Draws</th>
                  <th className="px-2 py-2">Updated</th>
                </tr>
              </thead>
              <tbody>
                {players.map((player, index) => (
                  <tr key={player.name} className="border-t border-border/50">
                    <td className="px-2 py-2 font-mono text-xs text-muted-foreground">{index + 1}</td>
                    <td className="px-2 py-2 font-semibold">{player.name}</td>
                    <td className="px-2 py-2 text-emerald-400">{player.wins}</td>
                    <td className="px-2 py-2 text-red-300">{player.losses}</td>
                    <td className="px-2 py-2">{player.draws}</td>
                    <td className="px-2 py-2 text-xs text-muted-foreground">{formatTs(player.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-border/60 bg-card p-5 shadow-sm">
        <div className="mb-3 flex items-center gap-2">
          <Trophy size={16} className="text-amber-400" />
          <h3 className="text-sm font-semibold">Recent matches</h3>
        </div>
        {(board?.matches?.length ?? 0) === 0 ? (
          <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            No matches recorded yet.
          </div>
        ) : (
          <div className="space-y-2">
            {(board?.matches ?? []).slice(0, 20).map((match) => (
              <div
                key={match.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/50 bg-muted/25 px-3 py-2 text-sm"
              >
                <span className="font-medium">{match.player_name}</span>
                <span className="text-xs text-muted-foreground">
                  {match.human_choice ?? '?'} vs {match.robot_choice ?? '?'} · winner: {match.winner}
                </span>
                <span className="text-[11px] text-muted-foreground">{formatTs(match.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}
