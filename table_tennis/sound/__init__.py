"""Table-tennis bounce audio.

Importing this package does not open a microphone, load a model, or send a
point proposal. A clip is a wav file plus the clock of its first sample.
"""

from table_tennis.sound.clip import (
    SAMPLE_RATE_HZ,
    AudioClip,
    read_clip,
    write_clip,
    write_impulse,
    write_silence,
)
from table_tennis.sound.peaks import energy_peaks
from table_tennis.sound.raw import RawBlock, clip_from_raw_blocks
from table_tennis.sound.sequence import missed_return_proposal
from table_tennis.sound.surface import classify_contact
from table_tennis.sound.sync import nearest_frame

__all__ = [
    "AudioClip",
    "RawBlock",
    "SAMPLE_RATE_HZ",
    "clip_from_raw_blocks",
    "classify_contact",
    "energy_peaks",
    "missed_return_proposal",
    "nearest_frame",
    "read_clip",
    "write_clip",
    "write_impulse",
    "write_silence",
]
