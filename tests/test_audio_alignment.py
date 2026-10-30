"""A coherent forged byte rate must not mask a false PCM block alignment."""
import unittest
from qa_lab.audio_pcm import AudioContractError, validate_pcm
from audio_fixtures import change, fixture


class AlignmentTests(unittest.TestCase):
    def test_alignment_is_checked_against_sample_width(self):
        raw = change(fixture(), 32, '<H', 1)
        raw = change(raw, 28, '<I', 16000)
        with self.assertRaisesRegex(AudioContractError, 'inconsistent_block_alignment'):
            validate_pcm(raw)
