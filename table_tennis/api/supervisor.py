"""Thin, disabled-by-default Supervisor integration (owner: integrator/person 2).

In robot_supervisor_v2/app/api/main.py:

    from table_tennis.api.supervisor import include_table_tennis
    include_table_tennis(app)

Nothing happens unless ``TABLE_TENNIS_ENABLED=1``. The Supervisor has no auth
of its own and usually binds to the network with CORS '*', so the feature is
only mounted when token auth is configured (TT_AUTH_MODE=token +
TT_OPERATOR_TOKEN...). The runtime starts lazily on the first request, so
importing/including never starts threads or opens the database.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from typing import Mapping, Optional

log = logging.getLogger("table_tennis.supervisor")

TRUTHY = {"1", "true", "yes", "on"}


def include_table_tennis(app, env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    if str(env.get("TABLE_TENNIS_ENABLED", "0")).strip().lower() not in TRUTHY:
        return False

    # Lazy imports: a disabled feature costs nothing at Supervisor startup.
    from table_tennis.config import load_settings

    from .router import build_router, install_error_handlers
    from .runtime import build_runtime

    try:
        settings = load_settings(env.get("TABLE_TENNIS_CONFIG") or None, env=env)
    except Exception as exc:
        log.error("table tennis feature NOT mounted: invalid config: %s", exc)
        return False
    if settings.auth.mode != "token":
        log.error(
            "table tennis feature NOT mounted: the Supervisor is network-facing; set TT_AUTH_MODE=token "
            "and TT_OPERATOR_TOKEN (plus TT_VISION_TOKEN / TT_ROBOT_TOKEN as needed)"
        )
        return False

    state: dict = {"runtime": None}
    lock = threading.Lock()

    def get_runtime():
        with lock:
            if state["runtime"] is None:
                runtime = build_runtime(settings)
                runtime.start()
                atexit.register(runtime.stop)
                state["runtime"] = runtime
            return state["runtime"]

    app.include_router(build_router(get_runtime))
    install_error_handlers(app)
    log.info("table tennis feature mounted at /api/table-tennis (mode=%s)", settings.mode)
    return True
