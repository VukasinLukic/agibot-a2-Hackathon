import unittest
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from humanoid_platform import (
    AudioBridgeMode,
    GestureBackend,
    HeadScreenBackend,
    PlatformId,
    RobotModelId,
    TemperatureMonitorBackend,
    VisualUiBackend,
    gesture_supported_models,
    get_gesture_spec,
    get_head_screen_spec,
    get_platform,
    get_robot_model,
    get_temperature_monitor_spec,
    get_visual_ui_spec,
    head_screen_supported_models,
    list_platforms,
    list_robot_models,
    robot_models_for_platform,
    temperature_monitor_supported_models,
    visual_ui_supported_models,
)


class HumanoidPlatformRegistryTests(unittest.TestCase):
    def test_current_platforms_are_registered(self) -> None:
        platform_ids = {platform.id for platform in list_platforms()}

        self.assertEqual(platform_ids, {PlatformId.AGIBOT, PlatformId.UNITREE})
        self.assertEqual(get_platform("agibot").display_name, "Agibot")

    def test_current_robot_models_are_registered(self) -> None:
        model_ids = {model.id for model in list_robot_models()}

        self.assertEqual(
            model_ids,
            {
                RobotModelId.AGIBOT_A2_ULTRA,
                RobotModelId.AGIBOT_X2_ULTRA,
                RobotModelId.UNITREE_G1_EDU,
            },
        )

    def test_x2_declares_remote_audio(self) -> None:
        model = get_robot_model(RobotModelId.AGIBOT_X2_ULTRA)

        self.assertEqual(model.platform, PlatformId.AGIBOT)
        self.assertEqual(model.audio_bridge.mode, AudioBridgeMode.REMOTE)
        self.assertTrue(
            any("PC3" in note for note in model.audio_bridge.notes),
            model.audio_bridge.notes,
        )

    def test_a2_declares_local_audio(self) -> None:
        model = get_robot_model(RobotModelId.AGIBOT_A2_ULTRA)

        self.assertEqual(model.platform, PlatformId.AGIBOT)
        self.assertEqual(model.audio_bridge.mode, AudioBridgeMode.LOCAL)

    def test_unitree_g1_is_reference_model_with_local_audio(self) -> None:
        model = get_robot_model(RobotModelId.UNITREE_G1_EDU)

        self.assertEqual(model.platform, PlatformId.UNITREE)
        self.assertEqual(model.audio_bridge.mode, AudioBridgeMode.LOCAL)
        self.assertTrue(any("legacy/reference" in note for note in model.notes))

    def test_filter_models_by_platform(self) -> None:
        agibot_models = {model.id for model in robot_models_for_platform("agibot")}
        unitree_models = {model.id for model in robot_models_for_platform("unitree")}

        self.assertEqual(
            agibot_models,
            {RobotModelId.AGIBOT_A2_ULTRA, RobotModelId.AGIBOT_X2_ULTRA},
        )
        self.assertEqual(unitree_models, {RobotModelId.UNITREE_G1_EDU})

    def test_temperature_monitor_support_is_currently_unitree_only(self) -> None:
        spec = get_temperature_monitor_spec(RobotModelId.UNITREE_G1_EDU)

        self.assertIsNotNone(spec)
        self.assertEqual(spec.backend, TemperatureMonitorBackend.UNITREE_LOWSTATE)
        self.assertEqual(
            temperature_monitor_supported_models(),
            (RobotModelId.UNITREE_G1_EDU,),
        )
        self.assertIsNone(get_temperature_monitor_spec(RobotModelId.AGIBOT_A2_ULTRA))
        self.assertIsNone(get_temperature_monitor_spec(RobotModelId.AGIBOT_X2_ULTRA))

    def test_gesture_support_is_unitree_and_agibot_a2(self) -> None:
        spec = get_gesture_spec(RobotModelId.UNITREE_G1_EDU)

        self.assertIsNotNone(spec)
        self.assertEqual(spec.backend, GestureBackend.UNITREE_G1_ARM_ACTIONS)
        self.assertEqual(spec.catalog_id, "unitree_g1_edu")
        self.assertEqual(
            set(gesture_supported_models()),
            {RobotModelId.UNITREE_G1_EDU, RobotModelId.AGIBOT_A2_ULTRA},
        )

        a2_spec = get_gesture_spec(RobotModelId.AGIBOT_A2_ULTRA)
        self.assertIsNotNone(a2_spec)
        self.assertEqual(a2_spec.backend, GestureBackend.AGIBOT_A2_MOTION_PLAYER)
        self.assertEqual(a2_spec.catalog_id, "agibot_a2_ultra")

        self.assertIsNone(get_gesture_spec(RobotModelId.AGIBOT_X2_ULTRA))

    def test_visual_ui_support_is_currently_unitree_only(self) -> None:
        spec = get_visual_ui_spec(RobotModelId.UNITREE_G1_EDU)

        self.assertIsNotNone(spec)
        self.assertEqual(spec.backend, VisualUiBackend.UNITREE_G1_AUDIO_LED)
        self.assertEqual(
            visual_ui_supported_models(),
            (RobotModelId.UNITREE_G1_EDU,),
        )
        self.assertIsNone(get_visual_ui_spec(RobotModelId.AGIBOT_A2_ULTRA))
        self.assertIsNone(get_visual_ui_spec(RobotModelId.AGIBOT_X2_ULTRA))

    def test_head_screen_support_is_currently_a2_only(self) -> None:
        spec = get_head_screen_spec(RobotModelId.AGIBOT_A2_ULTRA)

        self.assertIsNotNone(spec)
        self.assertEqual(spec.backend, HeadScreenBackend.AGIBOT_EMOTICON_PLAYER)
        self.assertEqual(
            head_screen_supported_models(),
            (RobotModelId.AGIBOT_A2_ULTRA,),
        )
        # X2 has the same kind of face screen but its emoticon player has not
        # been verified, and the G1 has no head screen at all.
        self.assertIsNone(get_head_screen_spec(RobotModelId.AGIBOT_X2_ULTRA))
        self.assertIsNone(get_head_screen_spec(RobotModelId.UNITREE_G1_EDU))


if __name__ == "__main__":
    unittest.main()
