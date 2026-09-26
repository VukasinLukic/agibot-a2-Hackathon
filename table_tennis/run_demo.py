"""Run the standalone mock backend.

    python -m table_tennis.run_demo --mode mock
    python -m table_tennis.run_demo --mode mock --port 8100 --db table_tennis/var/other.sqlite

Binds 127.0.0.1:8099 by default. Refuses a non-loopback host unless token auth
is configured. Reports a busy port instead of crashing with a traceback.
"""

from __future__ import annotations

import argparse
import socket
import sys


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host if host != "localhost" else "127.0.0.1", port))
        except OSError:
            return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A2 table tennis referee - standalone mock backend")
    parser.add_argument("--mode", choices=["mock", "real"], default=None, help="default: mock (from config)")
    parser.add_argument("--config", default=None, help="YAML config (see table_tennis/config.example.yaml)")
    parser.add_argument("--host", default=None, help="default 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="default 8099")
    parser.add_argument("--db", default=None, help="SQLite path (default table_tennis/var/table_tennis_mock.sqlite)")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    from table_tennis.config import check_bind_allowed, load_settings

    try:
        settings = load_settings(
            args.config,
            mode=args.mode,
            server__host=args.host,
            server__port=args.port,
            storage__db_path=args.db,
        )
        check_bind_allowed(settings)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    host, port = settings.server.host, settings.server.port
    if not port_is_free(host, port):
        print(f"ERROR: port {port} on {host} is already in use. Stop the other process or pass --port.", file=sys.stderr)
        return 2

    from table_tennis.api.runtime import RealModeNotAvailable, build_runtime

    try:
        runtime = build_runtime(settings)
    except RealModeNotAvailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    import uvicorn

    from table_tennis.api.app import create_app

    app = create_app(runtime=runtime)
    banner = "SIMULATION / MOCK MODE: no robot, camera or cloud calls" if settings.simulated else "REAL MODE"
    print(f"== A2 table tennis referee: {banner}")
    print(f"== API:    http://{host}:{port}/api/table-tennis/health")
    print(f"== Docs:   http://{host}:{port}/docs")
    print(f"== DB:     {settings.storage.db_path}")
    print(f"== Fake outputs log: {settings.outputs.fake_log_path}")
    uvicorn.run(app, host=host, port=port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
