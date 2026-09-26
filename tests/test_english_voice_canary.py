from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = REPO_ROOT / "livekit-client"
if str(CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CLIENT_ROOT))

from livekit_config.prompt_builder import PromptBuilder
from livekit_config import truebar_config
from livekit_config.voice_canary import english_voice_enabled
from robot_supervisor_v2.app.services.voice_agent import resolve_voice_agent_python
from robot_supervisor_v2.app import speech_config
from robot_supervisor_v2.app.speech_config import (
    DEFAULT_CUSTOM_TRANSFORMATIONS,
    DEFAULT_ENGLISH_TRANSFORMATIONS,
    english_locale_active,
    get_custom_transformations,
    migrate_legacy_speech_config,
    normalize_speech_config,
    normalize_text_for_tts,
    sanitize_spoken_text,
    validate_speech_config,
)
from voice_metrics import VoiceMetricsCollector, bind_voice_metrics


class _Context:
    def __init__(self) -> None:
        self.proc = SimpleNamespace(userdata={})


BASE_KEYS = {
    "SONIOX_API_KEY": "soniox-test",
    "ELEVEN_API_KEY": "eleven-test",
    "ELEVENLABS_VOICE_ID": "legacy-voice",
    "ELEVENLABS_MODEL": "eleven_multilingual_v2",
}


def _canary_env(**updates: str) -> dict[str, str]:
    # Clear experiment selectors inherited from a developer's real .env so
    # each factory test exercises only the arm declared by that test.
    values = {
        **BASE_KEYS,
        "VOICE_CANARY_ENABLED": "true",
        "STT_MODEL": "",
        "TTS_MODEL": "",
        "TTS_VOICE_ID": "",
        **updates,
    }
    return values


class EnglishVoiceCanaryTests(unittest.TestCase):
  def test_canary_interpreter_keeps_venv_entrypoint(self) -> None:
    with patch.dict(
        os.environ,
        {
            "VOICE_CANARY_ENABLED": "true",
            "VOICE_AGENT_PYTHON": ".venv-voice-canary/bin/python",
        },
        clear=False,
    ):
      self.assertTrue(
          resolve_voice_agent_python().endswith(
              "/.venv-voice-canary/bin/python"
          )
      )

  def test_off_path_ignores_provider_flags_and_keeps_legacy_factories(self) -> None:
    with patch.dict(
        os.environ,
        {**BASE_KEYS, "VOICE_CANARY_ENABLED": "false", "STT_PROVIDER": "unknown", "TTS_PROVIDER": "unknown"},
        clear=False,
    ), patch.object(truebar_config.soniox, "STT") as soniox_stt, patch.object(
        truebar_config.elevenlabs, "TTS"
    ) as eleven_tts:
        truebar_config.prepare_truebar_stt(_Context())
        truebar_config.prepare_truebar_tts(_Context())

    self.assertEqual(soniox_stt.call_count, 1)
    self.assertEqual(soniox_stt.call_args.kwargs["api_key"], "soniox-test")
    self.assertIsNone(soniox_stt.call_args.kwargs["params"].context)
    self.assertEqual(eleven_tts.call_count, 1)
    self.assertEqual(eleven_tts.call_args.kwargs, {
        "api_key": "eleven-test",
        "voice_id": "legacy-voice",
        "model": "eleven_multilingual_v2",
        "apply_language_text_normalization": True,
    })


  def test_stt_factory_dispatch(self) -> None:
    cases = [
        (
            "deepgram_flux",
            "livekit.plugins.deepgram.STTv2",
            {"DEEPGRAM_API_KEY": "dg", "STT_KEYTERMS": "Comtrade,TITAN"},
            {"model": "flux-general-en", "keyterm": ["Comtrade", "TITAN"]},
        ),
        (
            "assemblyai",
            "livekit.plugins.assemblyai.STT",
            {"ASSEMBLYAI_API_KEY": "aai", "STT_KEYTERMS": "Comtrade,TITAN"},
            {"model": "universal-streaming-english", "keyterms_prompt": ["Comtrade", "TITAN"]},
        ),
    ]
    for provider, target, extra, expected in cases:
      with self.subTest(provider=provider):
        env = _canary_env(STT_PROVIDER=provider, **extra)
        with patch.dict(os.environ, env, clear=False), patch(target) as constructor:
            truebar_config.prepare_truebar_stt(_Context())
        self.assertEqual(constructor.call_count, 1)
        for key, value in expected.items():
            self.assertEqual(constructor.call_args.kwargs[key], value)

  def test_soniox_canary_uses_shared_stt_model_surface(self) -> None:
    with patch.dict(
        os.environ,
        _canary_env(
            STT_PROVIDER="soniox",
            STT_MODEL="stt-rt-v4",
            SONIOX_LANGUAGE_HINTS="sr,en",
            STT_LANGUAGE_HINTS="en",
            SONIOX_LANGUAGE_HINTS_STRICT="true",
        ),
        clear=False,
    ), patch.object(truebar_config.soniox, "STT") as constructor:
        truebar_config.prepare_truebar_stt(_Context())
    options = constructor.call_args.kwargs["params"]
    self.assertEqual(options.model, "stt-rt-v4")
    self.assertEqual(options.language_hints, ["en"])
    self.assertTrue(options.language_hints_strict)

  def test_soniox_english_canary_supplies_domain_terms(self) -> None:
    with patch.dict(
        os.environ,
        _canary_env(
            STT_PROVIDER="soniox",
            STT_KEYTERMS="CERN,CERN openlab,Large Hadron Collider,EOS",
        ),
        clear=False,
    ), patch.object(truebar_config.soniox, "STT") as constructor:
        truebar_config.prepare_truebar_stt(_Context())
    options = constructor.call_args.kwargs["params"]
    self.assertEqual(
        options.context.terms,
        ["CERN", "CERN openlab", "Large Hadron Collider", "EOS"],
    )


  def test_tts_factory_dispatch(self) -> None:
    cases = [
        (
            "cartesia",
            "livekit.plugins.cartesia.TTS",
            {"CARTESIA_API_KEY": "cart", "TTS_VOICE_ID": "voice-a", "TTS_LANGUAGE": "en"},
            {"model": "sonic-3", "voice": "voice-a", "language": "en"},
        ),
        (
            "deepgram_aura2",
            "livekit.plugins.deepgram.TTS",
            {"DEEPGRAM_API_KEY": "dg"},
            {"model": "aura-2-andromeda-en"},
        ),
        (
            "soniox",
            "livekit.plugins.soniox.TTS",
            {"TTS_VOICE_ID": "Noah", "TTS_LANGUAGE": "sr"},
            {"model": "tts-rt-v1", "voice": "Noah", "language": "sr"},
        ),
        # Soniox has no per-utterance auto-detect, so the bilingual "auto"
        # setting has to resolve to a real code instead of being forwarded.
        (
            "soniox",
            "livekit.plugins.soniox.TTS",
            {"TTS_VOICE_ID": "Noah", "TTS_LANGUAGE": "auto"},
            {"model": "tts-rt-v1", "voice": "Noah", "language": "sr"},
        ),
        # An ElevenLabs model id left behind on a switched voice row must not
        # reach Soniox as a model name.
        (
            "soniox",
            "livekit.plugins.soniox.TTS",
            {"TTS_VOICE_ID": "Noah", "TTS_LANGUAGE": "en", "TTS_MODEL": "eleven_flash_v2_5"},
            {"model": "tts-rt-v1", "voice": "Noah", "language": "en"},
        ),
    ]
    for provider, target, extra, expected in cases:
      with self.subTest(provider=provider):
        env = _canary_env(TTS_PROVIDER=provider, **extra)
        with patch.dict(os.environ, env, clear=False), patch(target) as constructor:
            truebar_config.prepare_truebar_tts(_Context())
        self.assertEqual(constructor.call_count, 1)
        for key, value in expected.items():
            self.assertEqual(constructor.call_args.kwargs[key], value)


  def test_unknown_provider_raises_clearly(self) -> None:
    with patch.dict(os.environ, _canary_env(STT_PROVIDER="mystery"), clear=False):
        with self.assertRaisesRegex(ValueError, "Unknown STT_PROVIDER"):
            truebar_config.prepare_truebar_stt(_Context())


  def test_interpreter_switch_is_flag_gated(self) -> None:
    with patch.dict(os.environ, {"VOICE_CANARY_ENABLED": "false", "VOICE_AGENT_PYTHON": "/bad"}, clear=False):
        self.assertEqual(resolve_voice_agent_python(), sys.executable)
    canary_python = REPO_ROOT / ".venv-voice-canary" / "bin" / "python"
    with patch.dict(
        os.environ,
        {"VOICE_CANARY_ENABLED": "true", "VOICE_AGENT_PYTHON": str(canary_python)},
        clear=False,
    ):
        self.assertTrue(Path(resolve_voice_agent_python()).samefile(canary_python))


  def test_serbian_and_english_prompt_selection(self) -> None:
    with patch.dict(os.environ, {"VOICE_CANARY_ENABLED": "false", "VOICE_LANGUAGE": "en"}, clear=False):
        self.assertFalse(english_voice_enabled())
        serbian = PromptBuilder(include_examples=False).build()
        serbian_greeting = PromptBuilder(include_examples=False).build_initial_greeting()
    with patch.dict(
        os.environ,
        {"VOICE_CANARY_ENABLED": "true", "VOICE_LANGUAGE": "en", "VOICE_LOCALE": "en-US"},
        clear=False,
    ):
        english = PromptBuilder(include_examples=False).build()
        english_greeting = PromptBuilder(include_examples=False).build_initial_greeting()

    # Both prompt sets must mirror the visitor's language; they differ only in the
    # language the instructions themselves are authored in.
    self.assertIn("Uvek odgovaraj na jeziku na kojem ti se sagovornik obraća", serbian)
    self.assertTrue(serbian_greeting.startswith("Zdravo"))
    self.assertIn("Always answer in the same language the visitor is speaking", english)
    self.assertNotIn("Uvek odgovaraj na jeziku", english)
    self.assertTrue(english_greeting.startswith("Hello"))

  def test_neither_prompt_set_locks_the_reply_language(self) -> None:
    """Guards the regression this file used to assert: an English-only lock."""
    for canary in ("false", "true"):
      with self.subTest(canary=canary):
        with patch.dict(
            os.environ,
            {"VOICE_CANARY_ENABLED": canary, "VOICE_LANGUAGE": "en", "VOICE_LOCALE": "en-US"},
            clear=False,
        ):
            prompt = PromptBuilder(include_examples=False).build()
        for banned in (
            "Speak English only",
            "Never switch languages",
            "nastavi razgovor na engleskom",
        ):
            self.assertNotIn(banned, prompt)

  def test_hall_of_fame_tour_profile_layers_on_main(self) -> None:
    with patch.dict(
        os.environ,
        {"VOICE_CANARY_ENABLED": "true", "VOICE_LANGUAGE": "en", "VOICE_LOCALE": "en-US"},
        clear=False,
    ):
        builder = PromptBuilder(include_examples=False)
        prompt = builder.build(
            core_mode="main",
            persona="hall_of_fame_tour",
            context="hall_of_fame",
            phase="guided_tour",
        )
        greeting = builder.build_initial_greeting(
            core_mode="main",
            persona="hall_of_fame_tour",
            context="hall_of_fame",
            phase="guided_tour",
        )
    self.assertIn("You are TITAN", prompt)
    self.assertIn("retrieved panel applies only to the current question", prompt)
    self.assertIn("Every new visitor topic may retrieve any Hall of Fame panel", prompt)
    self.assertIn("Never ask which company", prompt)
    self.assertIn("A new named topic always replaces the previous panel", prompt)
    self.assertNotIn("{panel_context}", prompt)
    self.assertIn("Comtrade Hall of Fame", greeting)


  def test_face_quiz_and_survey_localize_only_inside_canary(self) -> None:
    with patch.dict(
        os.environ,
        {"VOICE_CANARY_ENABLED": "true", "VOICE_LANGUAGE": "en", "VOICE_LOCALE": "en-US"},
        clear=False,
    ):
        face = importlib.reload(importlib.import_module("face_identity_flow"))
        quiz = importlib.reload(importlib.import_module("quiz_flow"))
        survey = importlib.reload(importlib.import_module("survey_flow"))
        self.assertTrue(face.SAY_KNOWN_GREETING.startswith("Welcome back"))
        self.assertEqual(quiz.build_default_quiz().quiz_id, "comtrade-english-quiz")
        self.assertIn("The options are", quiz.build_default_quiz().questions[0].spoken_question)
        self.assertEqual(survey.build_default_survey().survey_id, "comtrade-visitor-survey-en")
        self.assertIn("Choose one option", survey.build_default_survey().questions[0].spoken_question)

    with patch.dict(
        os.environ,
        {"VOICE_CANARY_ENABLED": "false", "VOICE_LANGUAGE": "en"},
        clear=False,
    ):
        face = importlib.reload(face)
        quiz = importlib.reload(quiz)
        survey = importlib.reload(survey)
        self.assertEqual(face.SAY_KNOWN_GREETING, "Zdravo ponovo, {name}. Lepo je videti vas. Kako vam mogu pomoći?")
        self.assertEqual(quiz.build_default_quiz().quiz_id, "petrol-station-quiz")
        self.assertEqual(survey.build_default_survey().survey_id, "petrol-survey")


  def test_locale_specific_pronunciation_never_mixes_serbian_into_english(self) -> None:
    configured = {
        "custom_transformations": {"Comtrade": "Komtrejd"},
        "english_transformations": {"Comtrade": "Com-trade"},
    }
    with patch.object(speech_config, "load_speech_config", return_value=configured):
        sr = get_custom_transformations(locale="sr-RS")
        en = get_custom_transformations(locale="en-US")
    self.assertEqual(sr["Comtrade"], "Komtrejd")
    self.assertEqual(en["Comtrade"], "Com-trade")
    self.assertNotIn("Komtrejd", en.values())

  def test_ui_and_agent_read_the_same_pronunciation_bucket(self) -> None:
    """A Serbian voice under the English canary must still resolve to the English list."""
    configured = {
        "active_voice": {"provider": "soniox", "voice_id": "Noah", "language": "sr"},
        "custom_transformations": {"Comtrade": "NEVER-APPLIED"},
        "english_transformations": {"Comtrade": "Komtrejd"},
    }
    with patch.dict(
        os.environ,
        {"VOICE_CANARY_ENABLED": "true", "VOICE_LANGUAGE": "en", "VOICE_LOCALE": "en-US"},
        clear=False,
    ):
        self.assertTrue(english_locale_active())
        with patch.object(speech_config, "load_speech_config", return_value=configured):
            self.assertEqual(
                speech_config.normalize_text_for_tts("Comtrade"), "Komtrejd"
            )

    with patch.dict(
        os.environ,
        {"VOICE_CANARY_ENABLED": "false", "VOICE_LANGUAGE": "sr", "VOICE_LOCALE": "sr-RS"},
        clear=False,
    ):
        self.assertFalse(english_locale_active())
        with patch.object(speech_config, "load_speech_config", return_value=configured):
            self.assertEqual(
                speech_config.normalize_text_for_tts("Comtrade"), "NEVER-APPLIED"
            )

  def test_english_pronunciation_ships_no_built_in_entries(self) -> None:
    """Speech is spelled exactly as written unless an operator configures it."""
    self.assertEqual(DEFAULT_ENGLISH_TRANSFORMATIONS, {})
    self.assertEqual(DEFAULT_CUSTOM_TRANSFORMATIONS, {})
    with patch.object(speech_config, "load_speech_config", return_value={}):
        self.assertEqual(get_custom_transformations(locale="en-US"), {})
        spoken = "Comtrade System Integration TITAN AgiBot A2 Ultra CSI AI API LLM RAG Veselin"
        self.assertEqual(normalize_text_for_tts(spoken, locale="en-US"), spoken)


  def test_spoken_text_removes_emoji_without_changing_words(self) -> None:
    self.assertEqual(
        sanitize_spoken_text("Thank you—welcome! 🙂 How can I help? 🤖").strip(),
        "Thank you—welcome! How can I help?",
    )

  def test_spoken_text_never_leaks_active_panel_marker(self) -> None:
    self.assertEqual(
        sanitize_spoken_text("Comtrade was founded by Veselin. [ACTIVE PANEL]").strip(),
        "Comtrade was founded by Veselin.",
    )
    self.assertEqual(
        sanitize_spoken_text("Answer. [ active   panel ]").strip(),
        "Answer.",
    )
    self.assertEqual(
        sanitize_spoken_text(
            "Answer. [ RETRIEVED PANEL FOR THIS TURN ]"
        ).strip(),
        "Answer.",
    )


  def test_voice_contract_migration_is_explicit_truebar_only(self) -> None:
    legacy = {
        "active_voice": "tts:sr:milica",
        "voices": [{"label": "Milica", "tts_tag": "tts:sr:milica"}],
        "custom_transformations": {},
    }
    migrated = migrate_legacy_speech_config(legacy)
    self.assertEqual(migrated["active_voice"]["provider"], "truebar")
    self.assertEqual(migrated["active_voice"]["voice_id"], "tts:sr:milica")
    self.assertEqual(migrated["legacy_truebar_tts_tag"], "tts:sr:milica")
    normalized = normalize_speech_config(legacy)
    self.assertEqual(normalized["active_voice"]["provider"], "truebar")
    with self.assertRaisesRegex(ValueError, "explicit migrate"):
        validate_speech_config(legacy)


class _FakeSession:
    def __init__(self) -> None:
        self.listeners: dict[str, object] = {}

    def on(self, event: str, callback):
        self.listeners[event] = callback


class VoiceMetricsTests(unittest.TestCase):
  def test_metrics_off_has_no_listeners_or_awaits(self) -> None:
    session = _FakeSession()
    self.assertIsNone(bind_voice_metrics(session, enabled=False))
    self.assertEqual(session.listeners, {})


  def test_metrics_on_emits_one_privacy_safe_record_per_turn(self) -> None:
    session = _FakeSession()
    records: list[dict] = []
    VoiceMetricsCollector(session, emit=records.append)
    session.listeners["user_state_changed"](
        SimpleNamespace(old_state="speaking", new_state="listening", created_at=100.0)
    )
    session.listeners["user_input_transcribed"](
        SimpleNamespace(is_final=True, transcript="secret transcript", created_at=100.1)
    )
    session.listeners["agent_state_changed"](
        SimpleNamespace(old_state="thinking", new_state="speaking", created_at=100.5)
    )
    session.listeners["agent_state_changed"](
        SimpleNamespace(old_state="speaking", new_state="listening", created_at=101.0)
    )
    self.assertEqual(len(records), 1)
    self.assertEqual(records[0]["event"], "voice_turn_metrics")
    self.assertAlmostEqual(records[0]["stt_final"], 0.1)
    self.assertAlmostEqual(records[0]["first_audible_frame"], 0.5)
    serialized = str(records[0]).lower()
    self.assertNotIn("transcript", serialized)
    self.assertNotIn("biometric", serialized)
    self.assertNotIn("secret", serialized)


if __name__ == "__main__":
    unittest.main()
