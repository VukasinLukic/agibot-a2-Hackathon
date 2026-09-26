"""Company roles offered in the setup picker (corporate persona only).

The player picks a role voluntarily; it is never inferred from looks, voice or
name. The UI sends ``role_label`` (display text) and ``role_rank`` (1..7), and
the frontend keeps the same list in ``features/table-tennis/roles.ts``.
Unknown/free-text labels still work: they fall back to generic corporate lines.
"""

from __future__ import annotations

from typing import Optional

# key, label shown in the UI, rank (higher = more senior)
ROLES: tuple[tuple[str, str, int], ...] = (
    ("intern", "Pripravnik", 1),
    ("junior", "Junior", 2),
    ("senior", "Senior", 3),
    ("lead", "Team lead", 4),
    ("manager", "Menadžer", 5),
    ("director", "Direktor", 6),
    ("ceo", "CEO", 7),
)

_BY_LABEL = {label.casefold(): key for key, label, _ in ROLES}
_BY_KEY = {key: key for key, _, _ in ROLES}


def role_key(role_label: Optional[str]) -> Optional[str]:
    """Map a stored role_label to a known role key, or None for free text / no role."""
    if not role_label:
        return None
    norm = role_label.strip().casefold()
    return _BY_LABEL.get(norm) or _BY_KEY.get(norm)
