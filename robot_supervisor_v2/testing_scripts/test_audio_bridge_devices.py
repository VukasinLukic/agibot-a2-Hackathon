import unittest
from unittest import mock

import numpy as np

from robot_services.audio import audio_bridge


class AudioBridgeDeviceResolutionTests(unittest.TestCase):
    def test_augment_devices_attaches_alsa_metadata_without_duplicate(self) -> None:
        alsa_device = {
            "card": 4,
            "card_id": "RX",
            "card_name": "Wireless Microphone RX",
            "device": 0,
            "device_id": "USB Audio",
            "device_name": "USB Audio",
            "alsa_device": "plughw:CARD=RX,DEV=0",
            "id_path": "platform-usb-0:3.2.3:1.1",
            "id_path_tag": "platform_usb_0_3_2_3_1_1",
        }
        portaudio_devices = [
            {
                "index": 7,
                "name": "Wireless Microphone RX: USB Audio (hw:4,0)",
                "channels": 1,
                "sample_rate": 48000,
                "is_default": False,
                "hostapi": "ALSA",
            }
        ]

        with mock.patch.object(audio_bridge, "_query_alsa_capture_devices", return_value=[alsa_device]):
            devices = audio_bridge._augment_input_devices(portaudio_devices)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["index"], 7)
        self.assertEqual(devices[0]["alsa_device"], "plughw:CARD=RX,DEV=0")
        self.assertEqual(devices[0]["id_path"], "platform-usb-0:3.2.3:1.1")
        self.assertNotIn("alsa_only", devices[0])

    def test_augment_devices_includes_alsa_only_identity(self) -> None:
        alsa_device = {
            "card": 5,
            "card_id": "Speaker",
            "card_name": "Robot Speaker",
            "device": 0,
            "device_id": "USB Audio",
            "device_name": "USB Audio",
            "alsa_device": "plughw:CARD=Speaker,DEV=0",
        }

        with mock.patch.object(audio_bridge, "_query_alsa_playback_devices", return_value=[alsa_device]):
            devices = audio_bridge._augment_output_devices([])

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["index"], "plughw:CARD=Speaker,DEV=0")
        self.assertEqual(devices[0]["alsa_device"], "plughw:CARD=Speaker,DEV=0")
        self.assertTrue(devices[0]["alsa_only"])

    def test_resolves_alsa_identifier_to_portaudio_device(self) -> None:
        alsa_device = {
            "card": 4,
            "card_id": "RX",
            "card_name": "Wireless Microphone RX",
            "device": 0,
            "alsa_device": "plughw:CARD=RX,DEV=0",
        }
        portaudio_devices = [
            {"name": "Other Mic", "max_input_channels": 1, "max_output_channels": 0},
            {
                "name": "Wireless Microphone RX: USB Audio (hw:4,0)",
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
        ]

        with (
            mock.patch.object(audio_bridge.sd, "query_devices", return_value=portaudio_devices),
            mock.patch.object(audio_bridge, "_query_alsa_capture_devices", return_value=[alsa_device]),
        ):
            resolved = audio_bridge._resolve_audio_device("plughw:CARD=RX,DEV=0", "input")

        self.assertEqual(resolved, 1)

    def test_alsa_identifier_fails_if_no_portaudio_match_exists(self) -> None:
        alsa_device = {
            "card": 4,
            "card_id": "RX",
            "card_name": "Wireless Microphone RX",
            "device": 0,
            "alsa_device": "plughw:CARD=RX,DEV=0",
        }
        portaudio_devices = [
            {"name": "Other Mic", "max_input_channels": 1, "max_output_channels": 0},
        ]

        with (
            mock.patch.object(audio_bridge.sd, "query_devices", return_value=portaudio_devices),
            mock.patch.object(audio_bridge, "_query_alsa_capture_devices", return_value=[alsa_device]),
        ):
            with self.assertRaisesRegex(ValueError, "no matching PortAudio"):
                audio_bridge._resolve_audio_device("plughw:CARD=RX,DEV=0", "input")

    def test_extract_mono_input_samples_uses_single_selected_channel(self) -> None:
        samples = np.array(
            [
                [10, 20, 30, 40],
                [-10, -20, -30, -40],
            ],
            dtype=np.int16,
        )

        mono = audio_bridge._extract_mono_input_samples(samples, (2,))

        np.testing.assert_array_equal(mono, np.array([30, -30], dtype=np.int16))

    def test_extract_mono_input_samples_averages_selected_channels(self) -> None:
        samples = np.array(
            [
                [10, 20, 100, 200],
                [-10, -20, -100, -200],
            ],
            dtype=np.int16,
        )

        mono = audio_bridge._extract_mono_input_samples(samples, (0, 1))

        np.testing.assert_array_equal(mono, np.array([15, -15], dtype=np.int16))

    def test_extract_mono_input_samples_clips_to_int16(self) -> None:
        samples = np.array(
            [
                [40000, 40000],
                [-40000, -40000],
            ],
            dtype=np.int32,
        )

        mono = audio_bridge._extract_mono_input_samples(samples, (0, 1))

        np.testing.assert_array_equal(mono, np.array([32767, -32768], dtype=np.int16))

    def test_input_channel_validation_rejects_invalid_mix_channel(self) -> None:
        capture_channels = audio_bridge._normalize_input_capture_channels(4)

        with self.assertRaisesRegex(ValueError, "outside capture channel range"):
            audio_bridge._normalize_input_mix_channels([0, 4], capture_channels)


if __name__ == "__main__":
    unittest.main()
