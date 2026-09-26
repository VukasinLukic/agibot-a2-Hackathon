#!/usr/bin/env python
"""
Run the Robot Supervisor V2 API server.

Usage:
    python robot_supervisor_v2/run_api.py

    # Or with custom host/port
    python robot_supervisor_v2/run_api.py --host 0.0.0.0 --port 8080
    # use host 0.0.0.0 for remote access

    # With auto-reload for development
    python robot_supervisor_v2/run_api.py --reload
"""

import sys
from pathlib import Path

SUPERVISOR_ROOT = Path(__file__).parent
REPO_ROOT = SUPERVISOR_ROOT.parent

# Add import roots for both `app.*` and repo-level packages such as
# `humanoid_platform`.
for path in (SUPERVISOR_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import argparse
import asyncio
import copy
from datetime import datetime
import logging
import logging.config

import uvicorn

from app.supervisor_config import SupervisorConfigError, load_startup_config


SUPERVISOR_LOG_FILE = Path(__file__).parent / "logs" / "robot-supervisor.log"

# Suppress CancelledError tracebacks during graceful shutdown
class SuppressCancelledErrorFilter(logging.Filter):
    def filter(self, record):
        # Suppress tracebacks for CancelledError during shutdown
        if record.exc_info and record.exc_info[0] is asyncio.CancelledError:
            return False
        return True


def _append_handler(handlers, handler_name: str) -> list[str]:
    configured = list(handlers or [])
    if handler_name not in configured:
        configured.append(handler_name)
    return configured


def _build_log_config(log_file: Path) -> dict:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)

    formatters = log_config.setdefault("formatters", {})
    formatters.setdefault("default", {})["fmt"] = "%(levelprefix)s %(message)s"
    formatters.setdefault("access", {})["fmt"] = (
        '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    )
    formatters["supervisor_file_default"] = {
        "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
    }
    formatters["supervisor_file_access"] = {
        "()": "uvicorn.logging.AccessFormatter",
        "fmt": '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        "use_colors": False,
    }

    handlers = log_config.setdefault("handlers", {})
    handlers["supervisor_file"] = {
        "class": "logging.FileHandler",
        "formatter": "supervisor_file_default",
        "filename": str(log_file),
        "encoding": "utf-8",
    }
    handlers["supervisor_access_file"] = {
        "class": "logging.FileHandler",
        "formatter": "supervisor_file_access",
        "filename": str(log_file),
        "encoding": "utf-8",
    }

    log_config.setdefault("filters", {})["cancelled_error_filter"] = {
        "()": SuppressCancelledErrorFilter
    }
    for handler_config in handlers.values():
        handler_filters = handler_config.setdefault("filters", [])
        if "cancelled_error_filter" not in handler_filters:
            handler_filters.append("cancelled_error_filter")

    loggers = log_config.setdefault("loggers", {})
    uvicorn_logger = loggers.setdefault(
        "uvicorn",
        {"handlers": ["default"], "level": "INFO", "propagate": False},
    )
    uvicorn_logger["handlers"] = _append_handler(uvicorn_logger.get("handlers"), "supervisor_file")
    uvicorn_logger["propagate"] = False

    uvicorn_error_logger = loggers.setdefault("uvicorn.error", {"level": "INFO"})
    uvicorn_error_logger["handlers"] = _append_handler(
        uvicorn_error_logger.get("handlers", ["default"]),
        "supervisor_file",
    )
    uvicorn_error_logger["propagate"] = False

    uvicorn_access_logger = loggers.setdefault(
        "uvicorn.access",
        {"handlers": ["access"], "level": "INFO", "propagate": False},
    )
    uvicorn_access_logger["handlers"] = _append_handler(
        uvicorn_access_logger.get("handlers", ["access"]),
        "supervisor_access_file",
    )
    uvicorn_access_logger["propagate"] = False

    log_config["root"] = {
        "handlers": _append_handler(log_config.get("root", {}).get("handlers", ["default"]), "supervisor_file"),
        "level": "INFO",
    }

    return log_config


def _write_start_divider(log_file: Path, host: str, port: int, reload_enabled: bool) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    divider = (
        f"\n{'=' * 80}\n"
        f"=== ROBOT SUPERVISOR START at {timestamp} ===\n"
        f"=== Host: {host} Port: {port} Reload: {reload_enabled} ===\n"
        f"{'=' * 80}\n"
    )
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(divider)


def _load_api_defaults() -> tuple[str, int]:
    try:
        supervisor_config = load_startup_config().config
    except SupervisorConfigError as exc:
        print(f"Warning: unable to load supervisor config for API defaults: {exc}", file=sys.stderr)
        return "0.0.0.0", 8080

    return supervisor_config.api.host, supervisor_config.api.port


def main():
    default_host, default_port = _load_api_defaults()

    parser = argparse.ArgumentParser(description="Robot Supervisor V2 API Server")
    parser.add_argument("--host", default=default_host, help="Host to bind to")
    parser.add_argument("--port", type=int, default=default_port, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    log_config = _build_log_config(SUPERVISOR_LOG_FILE)
    _write_start_divider(SUPERVISOR_LOG_FILE, args.host, args.port, args.reload)
    logging.config.dictConfig(log_config)
    logger = logging.getLogger(__name__)
    logger.info("Starting Robot Supervisor V2 API server")
    logger.info("Host: %s", args.host)
    logger.info("Port: %s", args.port)
    logger.info("Reload: %s", args.reload)
    logger.info("API docs: http://%s:%s/docs", args.host, args.port)
    logger.info("Health: http://%s:%s/api/health", args.host, args.port)

    uvicorn.run(
        "app.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        timeout_graceful_shutdown=2,  # Only wait 2 seconds for connections to close
        log_config=log_config
    )


if __name__ == "__main__":
    main()
