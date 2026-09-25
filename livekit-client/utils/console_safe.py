import sys
from typing import Optional

_DEF_BLOCK_CHAR = "\u2588"
_FALLBACK_BLOCK_CHAR = "#"


def configure_utf8_output() -> None:
    """Ensure stdout/stderr won't crash on Unicode across platforms."""
    for stream in (sys.stdout, sys.stderr):
        try:
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Best-effort only; keep running even if reconfigure fails.
            pass


def supports_unicode(stream: Optional[object] = None) -> bool:
    target = stream or sys.stdout
    encoding = getattr(target, "encoding", None) or ""
    if not encoding:
        return False
    try:
        _DEF_BLOCK_CHAR.encode(encoding)
    except Exception:
        return False
    return True


def safe_block_char(stream: Optional[object] = None) -> str:
    return _DEF_BLOCK_CHAR if supports_unicode(stream) else _FALLBACK_BLOCK_CHAR
