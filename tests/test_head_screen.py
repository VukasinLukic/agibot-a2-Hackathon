import sys
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from robot_services.screen_manip import add_custom_message
from robot_services.screen_manip.emoticon_screen import (
    AgibotEmoticonScreenController,
    AgibotHeadScreenConfig,
    _sanitize,
)


class SanitizeTests(unittest.TestCase):
    def test_keeps_readable_readout_text(self) -> None:
        self.assertEqual(_sanitize("10:30", 24), "10:30")
        self.assertEqual(_sanitize("New York", 24), "New York")
        self.assertEqual(_sanitize("22°C", 24), "22°C")

    def test_collapses_whitespace_and_truncates(self) -> None:
        self.assertEqual(_sanitize("  10:30\n\tBelgrade ", 24), "10:30 Belgrade")
        self.assertEqual(_sanitize("A" * 40, 24), "A" * 24)

    def test_drops_characters_that_could_break_out_of_the_filtergraph(self) -> None:
        # The text can arrive from an LLM tool call, so shell/filtergraph
        # metacharacters must never survive to the renderer.
        self.assertEqual(_sanitize("10:30; rm -rf /", 24), "10:30 rm -rf /")
        self.assertEqual(_sanitize('a"b\\c', 24), "a b c")
        # Parentheses are kept on purpose (labels like "(GMT)"); the expansion
        # characters around them are what get dropped.
        self.assertEqual(_sanitize("$(id)`id`", 24), "(id) id")
        for dropped in ("$", "`", ";", "|", "&", "\\", '"', "*", "{", "}", "<", ">"):
            self.assertNotIn(dropped, _sanitize(f"10{dropped}30", 24))

    def test_empty_text_stays_empty(self) -> None:
        self.assertEqual(_sanitize("", 24), "")
        self.assertEqual(_sanitize("***", 24), "")


class RenderScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = AgibotEmoticonScreenController(AgibotHeadScreenConfig())
        self.scripts: list[str] = []

        def _capture(script: str, *, timeout_s: float) -> str:
            self.scripts.append(script)
            return ""

        self.controller._run_on_face_host = _capture  # type: ignore[assignment]

    def _render(self, primary: str, secondary: str = "", duration_s: float = 2.0) -> str:
        self.controller._render_clip(primary, secondary, duration_s)
        return self.scripts[-1]

    def test_render_script_shape(self) -> None:
        script = self._render("10:30", "Belgrade")

        # Without -nostdin, ffmpeg reads the remaining script lines off the
        # shared stdin and the shell loses the final rename.
        self.assertIn("-nostdin", script)
        # Text goes through files, never inline, so a ':' in "10:30" cannot
        # split the drawtext options.
        self.assertIn("textfile=", script)
        self.assertNotIn("drawtext=text=", script)
        # The clip is renamed into place so the face app never reads a
        # half-written file.
        self.assertIn("mv -f", script)
        self.assertIn(self.controller._slot_clip_path(), script)
        self.assertIn("s=800x480", script)

    def test_single_line_renders_one_centered_layer(self) -> None:
        script = self._render("10:30")

        self.assertEqual(script.count("drawtext="), 1)
        self.assertIn("y=(h-text_h)/2", script)

    def test_two_lines_render_stacked_layers(self) -> None:
        script = self._render("10:30", "Belgrade")

        self.assertEqual(script.count("drawtext="), 2)
        self.assertNotIn("y=(h-text_h)/2", script)

    def test_duration_has_a_floor(self) -> None:
        self.assertIn("d=0.50", self._render("10:30", duration_s=0.0))
        self.assertIn("d=3.00", self._render("10:30", duration_s=3.0))

    def test_text_is_shell_quoted_into_the_script(self) -> None:
        # _render_clip is the last line of defence even if a caller skips
        # _sanitize, so the text must still be quoted rather than interpolated.
        script = self._render("a b; touch /tmp/pwned", "")

        self.assertNotIn("; touch /tmp/pwned >", script)
        self.assertIn("'a b; touch /tmp/pwned'", script)


class BackendSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (
            add_custom_message._controller,
            add_custom_message._controller_disabled,
        )
        add_custom_message._controller = None
        add_custom_message._controller_disabled = False

    def tearDown(self) -> None:
        (
            add_custom_message._controller,
            add_custom_message._controller_disabled,
        ) = self._saved

    def test_model_without_a_head_screen_disables_cleanly(self) -> None:
        with unittest.mock.patch.dict(
            "os.environ", {"HUMANOID_ROBOT_MODEL": "unitree_g1_edu"}
        ):
            self.assertIsNone(add_custom_message.get_screen_controller())
            result = add_custom_message.show_message("10:30", "Belgrade")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "head_screen_unavailable")

    def test_unknown_model_disables_cleanly(self) -> None:
        with unittest.mock.patch.dict(
            "os.environ", {"HUMANOID_ROBOT_MODEL": "not_a_robot"}
        ):
            self.assertIsNone(add_custom_message.get_screen_controller())

    def test_a2_selects_the_emoticon_backend(self) -> None:
        with unittest.mock.patch.dict(
            "os.environ", {"HUMANOID_ROBOT_MODEL": "agibot_a2_ultra"}
        ):
            controller = add_custom_message.get_screen_controller()

        self.assertIsInstance(controller, AgibotEmoticonScreenController)


if __name__ == "__main__":
    unittest.main()
