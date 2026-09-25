import unittest
from unittest import mock

from robot_supervisor_v2.app.services.base import BaseService, ServiceState


class DummyService(BaseService):
    async def start(self) -> None:
        self._state = ServiceState.RUNNING
        self._mark_started()

    async def stop(self) -> None:
        self._state = ServiceState.STOPPED
        self._start_time = None

    async def check_health(self) -> bool:
        return True

    def get_log_path(self) -> str | None:
        return None


class ServiceUptimeTests(unittest.TestCase):
    def test_uptime_uses_monotonic_start_time(self):
        service = DummyService("dummy", "Dummy", {})
        service._state = ServiceState.RUNNING
        service._start_time = 100.0

        with mock.patch("robot_supervisor_v2.app.services.base.time.monotonic", return_value=112.5):
            status = service.get_status()

        self.assertEqual(status.uptime_seconds, 12.5)

    def test_uptime_accepts_legacy_wall_clock_start_time(self):
        service = DummyService("dummy", "Dummy", {})
        service._state = ServiceState.RUNNING
        service._start_time = 1_750_000_000.0

        with (
            mock.patch("robot_supervisor_v2.app.services.base.time.monotonic", return_value=100.0),
            mock.patch("robot_supervisor_v2.app.services.base.time.time", return_value=1_750_000_009.25),
        ):
            status = service.get_status()

        self.assertEqual(status.uptime_seconds, 9.25)


if __name__ == "__main__":
    unittest.main()
