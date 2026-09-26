import type { MatchSnapshot, PlayerId } from '../generated/contract';

interface ScoreboardProps {
  snapshot: MatchSnapshot;
  /** When set, each half of the table is the "point for this player" button. */
  onPoint?: (player: PlayerId) => void;
  disabled?: boolean;
}

/**
 * The table seen from above: one half per player, the net between them.
 * The half of the player standing left of the robot is on top. Scores come
 * only from the backend snapshot; tapping a half just sends a command.
 */
export function Scoreboard({ snapshot, onPoint, disabled }: ScoreboardProps) {
  const order: PlayerId[] = snapshot.robot_side_by_player.p1 === 'left' ? ['p1', 'p2'] : ['p2', 'p1'];

  return (
    <section aria-label="Rezultat" className="tt-table">
      {order.map((id) => {
        const player = snapshot.players.find((p) => p.id === id);
        const name = player?.display_name ?? id;
        const serving = snapshot.server_id === id && snapshot.status !== 'finished';
        const content = (
          <>
            <span className="flex max-w-full items-center gap-2">
              {serving && <span className="tt-ball" aria-label="servira" />}
              <span className="tt-name">{name}</span>
            </span>
            {player?.role_label && <span className="text-xs opacity-60">{player.role_label}</span>}
            {/* key = score: a changed number remounts and replays the short highlight */}
            <span key={snapshot.score_by_player[id]} className="tt-score" data-bump="true" aria-live="polite">
              {snapshot.score_by_player[id]}
            </span>
            {onPoint && <span className="tt-tap">+ poen</span>}
          </>
        );
        return onPoint ? (
          <button
            key={id}
            type="button"
            className="tt-half"
            disabled={disabled}
            onClick={() => onPoint(id)}
            aria-label={`Poen za ${name}`}
          >
            {content}
          </button>
        ) : (
          <div key={id} className="tt-half" data-winner={snapshot.winner_id === id}>
            {content}
          </div>
        );
      })}
      <span className="tt-net" aria-hidden="true" />
    </section>
  );
}

