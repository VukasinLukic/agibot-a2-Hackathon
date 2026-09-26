"""Robot system clock: what time it is, in which zone, and how trustworthy it is.

WHY THIS IS ESSENTIALLY FREE, AND HOW IT AVOIDS BEING EXPENSIVE
---------------------------------------------------------------
The obvious implementation — the browser asking the server for the time once a
second — is the one to avoid. Measured on this robot (2026-09-10):

    timedatectl show   ~123 ms per call   (a D-Bus round trip to systemd)
    date               ~5.8 ms per call   (a fork + exec)

and the machine already runs at load ~29 on 12 cores (perception 120%, recordbag
62%, slam 43%, pnc 39%). Spawning a subprocess every second for a wall clock would
be pure waste on a box with no headroom.

So instead: this endpoint is called RARELY (on load, then every RESYNC_HINT_S), it
returns the server's epoch time plus the zone, and the browser ticks locally off
its own clock corrected by the measured offset. Steady-state server cost is zero.

`timedatectl` is only invoked on an explicit sync, and its output is cached for
ZONE_CACHE_S — the timezone does not change on its own.

THE CLOCK ON THIS ROBOT IS NOT NTP-SYNCED
-----------------------------------------
`timedatectl status` reports `System clock synchronized: no` and
`NTP service: inactive`, with RTC and system clock already ~5 s apart. Anything
that acts at a wall-clock time (see the mission scheduler) is therefore acting on
a free-running clock that can drift from real time. That is not something this
module can fix, but it must not hide it either — `synchronized` and `ntp_active`
are reported so the UI can say so plainly.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system-clock", tags=["system-clock"])

# The sync flags are the only thing that needs `timedatectl`, and they change
# rarely, so they get a long TTL. Everything else on the hot path is subprocess
# free — measured on this robot: `timedatectl show` 716 ms under load, versus
# 0.16 ms to read the zone name from the /etc/localtime symlink.
SYNC_CACHE_S = 300.0
RESYNC_HINT_S = 60         # how often we suggest the browser re-syncs
TIMEDATECTL_TIMEOUT_S = 8.0

_sync_cache: dict[str, Any] = {}
_sync_cache_at: float = 0.0


def _run(args: list[str], timeout: float = TIMEDATECTL_TIMEOUT_S) -> str:
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=timeout).stdout


def _local_zone_name() -> str:
    """IANA zone name without spawning anything.

    /etc/localtime is a symlink into the zoneinfo database on this system, and
    /etc/timezone holds the same string; either is ~0.2 ms versus ~700 ms for
    `timedatectl show` on a loaded box.
    """
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        name = Path("/etc/timezone").read_text().strip()
        if name:
            return name
    except OSError:
        pass
    return time.tzname[0] if time.tzname else "UTC"


def _read_sync_state() -> dict[str, Any]:
    """NTP / clock-trust flags from systemd. Cached for SYNC_CACHE_S."""
    global _sync_cache, _sync_cache_at
    if _sync_cache and (time.monotonic() - _sync_cache_at) < SYNC_CACHE_S:
        return _sync_cache
    info: dict[str, str] = {}
    try:
        for line in _run(["timedatectl", "show"]).splitlines():
            k, _, v = line.partition("=")
            info[k.strip()] = v.strip()
    except Exception as exc:
        logger.warning("timedatectl show failed: %s", exc)
    out = {
        "synchronized": info.get("NTPSynchronized") == "yes",
        "ntp_active": info.get("NTPService") in ("active", "yes")
                      or info.get("NTP") == "yes",
        "rtc_in_local_tz": info.get("LocalRTC") == "yes",
    }
    _sync_cache, _sync_cache_at = out, time.monotonic()
    return out


_last_zone_seen: str = ""


def _sync_process_tz(zone: str) -> None:
    """Make THIS process notice that the system timezone changed.

    Necessary because the two halves of the payload have different sources:
    the zone NAME is re-read from /etc/localtime every call (cheap, always
    current), but the OFFSET comes from `datetime.astimezone()`, which uses
    glibc's cached tzinfo and does not change until `tzset()`. Without this the
    endpoint would report the NEW zone name alongside the OLD offset and OLD
    local time after a change — more misleading than not updating at all.

    With it, changing the zone by hand (`sudo timedatectl set-timezone ...`)
    is picked up by a RUNNING supervisor on its next clock call, no restart.
    Other robot services still need restarting; they cache it the same way.
    """
    global _last_zone_seen
    if zone and zone != _last_zone_seen:
        os.environ["TZ"] = zone
        try:
            time.tzset()
        except AttributeError:      # not POSIX; nothing to do
            pass
        _last_zone_seen = zone


def _now_payload() -> dict[str, Any]:
    _sync_process_tz(_local_zone_name())
    now = time.time()
    local = datetime.fromtimestamp(now).astimezone()
    sync = _read_sync_state()
    offset = local.utcoffset()
    return {
        # Epoch seconds with sub-second precision: the browser diffs this against
        # its own clock once and then ticks locally, so it can show seconds
        # accurately without ever asking again.
        "epoch": round(now, 3),
        "iso_local": local.isoformat(timespec="seconds"),
        "iso_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(
            timespec="seconds"),
        "timezone": _local_zone_name(),
        "abbreviation": local.tzname(),
        "utc_offset_seconds": int(offset.total_seconds()) if offset else 0,
        "synchronized": sync["synchronized"],
        "ntp_active": sync["ntp_active"],
        "resync_after_s": RESYNC_HINT_S,
    }


@router.get("")
async def get_clock() -> dict:
    """Server time + zone. Call on load and every `resync_after_s`, not per second."""
    return await asyncio.to_thread(_now_payload)


# Drift check. NOT on the hot path and NOT automatic: it makes an outbound HTTPS
# request, so it runs only when asked and is cached for DRIFT_CACHE_S.
DRIFT_CACHE_S = 600.0
DRIFT_REFERENCE = "https://www.google.com"
_drift_cache: dict[str, Any] = {}
_drift_cache_at: float = 0.0


def _measure_drift() -> dict[str, Any]:
    """How far the robot's clock is from a trusted reference, in seconds.

    Uses the HTTP `Date` response header, halving out the round trip. That header
    has 1-second resolution, so treat anything under ~2 s as noise — it is meant
    to catch "the clock is minutes or hours out", which is what actually breaks a
    scheduled start, not to discipline the clock.
    """
    global _drift_cache, _drift_cache_at
    if _drift_cache and (time.monotonic() - _drift_cache_at) < DRIFT_CACHE_S:
        return _drift_cache
    import email.utils
    import urllib.request
    out: dict[str, Any]
    try:
        t0 = time.time()
        resp = urllib.request.urlopen(
            urllib.request.Request(DRIFT_REFERENCE, method="HEAD"), timeout=10)
        t1 = time.time()
        server = email.utils.parsedate_to_datetime(resp.headers["Date"]).timestamp()
        out = {
            "ok": True,
            "reference": DRIFT_REFERENCE,
            # Positive = the robot is AHEAD of real time.
            "drift_seconds": round((t0 + t1) / 2 - server, 1),
            "round_trip_ms": round((t1 - t0) * 1000),
            "resolution_note": "the reference has 1 s resolution; <2 s is noise",
            "checked_at": round(time.time(), 1),
        }
    except Exception as exc:
        out = {"ok": False, "reference": DRIFT_REFERENCE,
               "error": f"{type(exc).__name__}: {exc}",
               "checked_at": round(time.time(), 1)}
    _drift_cache, _drift_cache_at = out, time.monotonic()
    return out


@router.get("/drift")
async def clock_drift() -> dict:
    """Compare the robot's clock to an internet reference (cached 10 min).

    Worth checking before relying on a scheduled start: this robot has no NTP, so
    its clock free-runs and nothing else will tell you it has wandered.
    """
    return await asyncio.to_thread(_measure_drift)


@router.get("/zones")
async def list_zones() -> dict:
    """Available IANA zones, for the picker."""
    def work() -> list[str]:
        try:
            out = _run(["timedatectl", "list-timezones"], timeout=15.0)
            zones = [z for z in out.split() if z]
            if zones:
                return zones
        except Exception:
            logger.warning("timedatectl list-timezones failed", exc_info=True)
        # zoneinfo is in the stdlib from 3.9 and needs no subprocess at all.
        try:
            import zoneinfo
            return sorted(zoneinfo.available_timezones())
        except Exception:
            return []
    zones = await asyncio.to_thread(work)
    return {"zones": zones, "count": len(zones)}


class SetTimezone(BaseModel):
    timezone: str = Field(..., min_length=1, max_length=64)


@router.post("/timezone")
async def set_timezone(body: SetTimezone) -> dict:
    """Change the system timezone.

    REQUIRES A SUDOERS RULE, and this robot does not have one yet: `sudo -n -l`
    reports that a password is required, and /etc/sudoers.d holds only its README.
    Rather than fail with a raw "sudo: a password is required", we check first and
    explain, mirroring how the Network Manager feature is set up
    (setup_network_manager_sudoers.sh).

    TWO THINGS THE CALLER MUST KNOW, both verified on 2026-09-10:
      * Already-running processes keep the OLD zone. glibc/Python cache it until
        `tzset()`, so the robot's services (pnc, slam, gateway, the supervisor
        itself) go on logging in the previous timezone until they are restarted.
        Log timestamps will disagree with this clock until then.
      * The clock is not NTP-synced here, so changing the zone changes the label
        and the offset, not the underlying accuracy.
    """
    def work() -> dict:
        zones_out = _run(["timedatectl", "list-timezones"], timeout=15.0).split()
        if zones_out and body.timezone not in zones_out:
            raise HTTPException(400, f"unknown timezone {body.timezone!r}")
        probe = subprocess.run(["sudo", "-n", "timedatectl", "set-timezone",
                                body.timezone],
                               capture_output=True, text=True, timeout=15.0)
        if probe.returncode != 0:
            err = (probe.stderr or "").strip()
            if "password" in err.lower() or "not allowed" in err.lower():
                raise HTTPException(
                    501,
                    "changing the timezone needs a passwordless sudo rule for "
                    "`timedatectl set-timezone`, which this robot does not have. "
                    "Add one the same way the Network Manager feature does "
                    "(see robot_supervisor_v2/setup_network_manager_sudoers.sh), "
                    f"then retry. sudo said: {err[:160]}")
            raise HTTPException(500, f"timedatectl failed: {err[:200]}")
        return {"timezone": body.timezone}

    result = await asyncio.to_thread(work)
    global _sync_cache_at
    _sync_cache_at = 0.0        # force a re-read on the next clock call
    return {**result, **await asyncio.to_thread(_now_payload),
            "note": "Running services keep the previous timezone until restarted, "
                    "so their log timestamps will not match this clock yet."}
