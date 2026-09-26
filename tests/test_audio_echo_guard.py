import unittest

from robot_services.audio.echo_guard import capture_should_be_suppressed


class AudioEchoGuardTests(unittest.TestCase):
    def test_echo_guard_is_active_during_playback_tail(self) -> None:
        self.assertTrue(capture_should_be_suppressed(
            enabled=True,
            now=10.4,
            last_playback_at=10.0,
            tail_seconds=0.5,
        ))

    def test_echo_guard_reopens_microphone_after_tail(self) -> None:
        self.assertFalse(capture_should_be_suppressed(
            enabled=True,
            now=10.6,
            last_playback_at=10.0,
            tail_seconds=0.5,
        ))

    def test_echo_guard_off_never_suppresses_capture(self) -> None:
        self.assertFalse(capture_should_be_suppressed(
            enabled=False,
            now=10.1,
            last_playback_at=10.0,
            tail_seconds=0.5,
        ))


if __name__ == "__main__":
    unittest.main()
