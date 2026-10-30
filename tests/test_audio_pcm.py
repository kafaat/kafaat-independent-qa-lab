"""Behavioural parser, file lifecycle and negative controls for owned WAV data."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from qa_lab import audio_pcm as audio
from audio_fixtures import cases, change, chunk, fixture, riff


class CorpusTests(unittest.TestCase):
    pass


def corpus_test(case):
    def test(self):
        before = case['raw']
        if case['accepted']:
            metrics = audio.validate_pcm(before, **case['options'])
            self.assertEqual(metrics['sha256'], hashlib.sha256(before).hexdigest())
            self.assertEqual(metrics['size_bytes'], len(before))
            self.assertTrue(metrics['finite_samples'])
            self.assertEqual(metrics['integrity'], 'VALID_INTEGER_PCM')
        else:
            with self.assertRaises(audio.AudioContractError):
                audio.validate_pcm(before, **case['options'])
        self.assertEqual(before, case['raw'])
    return test


for case in cases():
    setattr(CorpusTests, 'test_' + case['id'].replace('-', '_'), corpus_test(case))


class MetricTests(unittest.TestCase):
    def test_unsigned_samples_center_on_128(self):
        metrics = audio.validate_pcm(fixture(width=1, amplitude=-128))
        self.assertEqual(metrics['peak_integer'], 128)
        self.assertEqual(metrics['rms_integer'], 128)

    def test_exact_constant_samples(self):
        metrics = audio.validate_pcm(fixture(width=3, channels=2))
        self.assertEqual(metrics['frames'], 1600)
        self.assertEqual(metrics['duration_seconds'], 0.1)
        self.assertEqual(metrics['peak_integer'], 23)
        self.assertEqual(metrics['rms_integer'], 23)

    def test_nonconstant_rms(self):
        raw = fixture(frames=16)
        values = [3, -4] * 8
        raw = raw[:44] + struct.pack('<16h', *values)
        self.assertEqual(audio.validate_pcm(raw)['rms_integer'], (12.5) ** 0.5)

    def test_normal_pcm_from_stdlib_writer(self):
        import wave
        output = io.BytesIO()
        with wave.open(output, 'wb') as writer:
            writer.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            writer.writeframes(b'\1\0\2\0' * 4800)
        self.assertEqual(audio.validate_pcm(output.getvalue())['duration_seconds'], 0.1)


class BoundaryTests(unittest.TestCase):
    def test_invalid_options(self):
        invalid = [{'channels': True}, {'sample_rate': False}, {'sample_rate': 1},
                   {'min_duration': 0}, {'max_duration': 3601}, {'min_duration': -1},
                   {'max_duration': float('nan')}, {'max_duration': float('inf')},
                   {'max_duration': 10 ** 1000}, {'min_duration': '0.1'}]
        for options in invalid:
            with self.subTest(options=str(options)[:80]), self.assertRaises(audio.AudioContractError):
                audio.validate_pcm(fixture(), **options)

    def test_requires_immutable_bytes(self):
        for value in (None, 'RIFF', bytearray(fixture()), memoryview(fixture())):
            with self.subTest(kind=type(value)), self.assertRaises(audio.AudioContractError):
                audio.validate_pcm(value)

    def test_size_limit(self):
        with patch.object(audio, 'MAX_BYTES', 100), self.assertRaisesRegex(
                audio.AudioContractError, 'invalid_size'):
            audio.validate_pcm(fixture())

    def test_chunk_count_limit(self):
        raw = fixture()
        many = riff(*([chunk(b'JUNK', b'')] * 4097),
                    chunk(b'fmt ', raw[20:36]), chunk(b'data', raw[44:]))
        with self.assertRaisesRegex(audio.AudioContractError, 'too_many_chunks'):
            audio.validate_pcm(many)

    def test_chunk_length_overflow(self):
        raw = change(fixture(), 40, '<I', 0xFFFFFFFF)
        with self.assertRaises(audio.AudioContractError):
            audio.validate_pcm(raw)

    def test_truncated_chunk_header(self):
        raw = riff(chunk(b'fmt ', fixture()[20:36]), chunk(b'data', fixture()[44:]), b'x')
        with self.assertRaisesRegex(audio.AudioContractError, 'truncated_chunk_header'):
            audio.validate_pcm(raw)

    def test_missing_odd_pad(self):
        raw = riff(chunk(b'fmt ', fixture()[20:36]), b'JUNK\3\0\0\0abc')
        with self.assertRaises(audio.AudioContractError):
            audio.validate_pcm(raw)

    def test_extensible_non_pcm_guid(self):
        with self.assertRaisesRegex(audio.AudioContractError, 'unsupported_extensible_format'):
            audio.validate_pcm(change(fixture(extensible=True), 44, '<I', 3))

    def test_extended_format_size(self):
        fmt = fixture(extensible=True)[20:60]
        for bad in (fmt[:-1], fmt + b'xx'):
            with self.assertRaises(audio.AudioContractError):
                audio.validate_pcm(riff(chunk(b'fmt ', bad), chunk(b'data', b'\1\0' * 1600)))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'input.wav'
        self.path.write_bytes(fixture())

    def test_regular_file_unchanged(self):
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        result = audio.validate_wav(self.path)
        self.assertEqual(result['frames'], 1600)
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_missing_file_sanitized(self):
        with self.assertRaisesRegex(audio.AudioContractError, '^input_unavailable$'):
            audio.validate_wav(self.path.parent / 'secret-not-in-error.wav')

    def test_directory_rejected(self):
        with self.assertRaises(audio.AudioContractError):
            audio.validate_wav(self.path.parent)

    def test_symlink_rejected(self):
        link = self.path.parent / 'alias.wav'
        link.symlink_to(self.path)
        with self.assertRaises(audio.AudioContractError):
            audio.validate_wav(link)

    def test_fifo_rejected_without_open(self):
        fifo = self.path.parent / 'pipe.wav'
        os.mkfifo(fifo)
        with patch.object(audio.os, 'open') as opening:
            with self.assertRaises(audio.AudioContractError):
                audio.validate_wav(fifo)
            opening.assert_not_called()

    def test_oversized_file_rejected_without_open(self):
        with patch.object(audio, 'MAX_BYTES', 100), patch.object(audio.os, 'open') as opening:
            with self.assertRaises(audio.AudioContractError):
                audio.validate_wav(self.path)
            opening.assert_not_called()

    def test_changed_inode_rejected(self):
        actual = os.fstat
        def altered(fd):
            row = actual(fd)
            data = list(row)
            data[1] += 1
            return os.stat_result(data)
        with patch.object(audio.os, 'fstat', side_effect=altered):
            with self.assertRaisesRegex(audio.AudioContractError, 'input_changed'):
                audio.validate_wav(self.path)

    def test_cli_success_scope(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(audio.main(['--wav', str(self.path)]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report['status'], 'PASSED')
        self.assertFalse(report['production_accepted'])
        self.assertFalse(report['asr_tested'])
        self.assertFalse(report['tts_quality_tested'])
        self.assertNotIn(str(self.path), output.getvalue())

    def test_cli_failure_scope(self):
        self.path.write_bytes(b'private-invalid-data')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(audio.main(['--wav', str(self.path)]), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'FAILED')
        self.assertNotIn('private-invalid-data', output.getvalue())

    def test_repeated_reads_close_descriptors(self):
        with patch.object(audio.os, 'fdopen', wraps=os.fdopen) as opening:
            for _ in range(10):
                audio.validate_wav(self.path)
        self.assertEqual(opening.call_count, 10)


if __name__ == '__main__':
    unittest.main()
