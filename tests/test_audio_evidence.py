"""Recheck archived comparison against new code; does NOT execute legacy code."""
import hashlib
import json
from pathlib import Path
import unittest
from audio_fixtures import cases
from qa_lab import audio_pcm
from qa_lab.parity import content_digest

ROOT = Path(__file__).resolve().parents[1]


class ArchivedAudioEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads((ROOT / 'docs/evidence/audio-pcm-20261030/summary.json').read_text())

    def test_fixture_source_matches_archive(self):
        actual = hashlib.sha256((ROOT / 'tests/audio_fixtures.py').read_bytes()).hexdigest()
        self.assertEqual(actual, self.summary['fixture_generator_sha256'])

    def test_new_implementation_matches_archive(self):
        actual = hashlib.sha256((ROOT / 'qa_lab/audio_pcm.py').read_bytes()).hexdigest()
        self.assertEqual(actual, self.summary['source_manifests']['new']['files']['audio_pcm.py'])

    def test_common_input_is_exactly_pinned(self):
        common = [c for c in cases() if c['group'] == 'compatibility']
        expected = self.summary['compatibility_input']
        manifest = [{'id':c['id'],'sha256':hashlib.sha256(c['raw']).hexdigest(),
                     'options':c['options']} for c in common]
        self.assertEqual(len(manifest), 41)
        self.assertEqual(manifest, expected['fixtures'])
        self.assertEqual(content_digest(expected), self.summary['compatibility_input_digest'])

    def test_new_results_match_recorded_legacy_output(self):
        output = {}
        for c in cases():
            if c['group'] != 'compatibility':
                continue
            try:
                metrics = audio_pcm.validate_pcm(c['raw'], **c['options'])
                result = {'decision':'ACCEPTED','metrics':metrics}
            except audio_pcm.AudioContractError:
                result = {'decision':'REJECTED'}
            output[c['id']] = result
        digest = content_digest({'cases': output})
        self.assertEqual(digest, self.summary['new_output_digest'])
        self.assertEqual(digest, self.summary['legacy_output_digest'])

    def test_scope_and_differences_are_not_promoted(self):
        data = self.summary
        self.assertFalse(data['full_audio_profile_verified'])
        self.assertFalse(data['deployed_recipe_registered'])
        self.assertFalse(data['production_accepted'])
        self.assertFalse(data['summary_is_complete_v2_receipt'])
        self.assertEqual(data['legacy_cli_parity'], 'NOT_RUN')
        self.assertEqual(len(data['differences']), 6)
        fixture_map = {c['id']:c for c in cases()}
        for diff in data['differences']:
            fixture = fixture_map[diff['id']]
            self.assertNotEqual(fixture['group'], 'compatibility')
            self.assertEqual(diff['input_sha256'], hashlib.sha256(fixture['raw']).hexdigest())
            with self.assertRaises(audio_pcm.AudioContractError):
                audio_pcm.validate_pcm(fixture['raw'], **fixture['options'])
