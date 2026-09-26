"""Pure timing policy for suppressing acoustic self-echo capture."""


def capture_should_be_suppressed(
    *,
    enabled: bool,
    now: float,
    last_playback_at: float,
    tail_seconds: float,
) -> bool:
    """Return whether capture falls inside the speaker playback echo window."""
    if not enabled or last_playback_at < 0:
        return False
    return now - last_playback_at <= tail_seconds
