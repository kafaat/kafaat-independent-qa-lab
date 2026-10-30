"""Bounded, offline integer-PCM evidence validator, not speech-quality acceptance.

Parses RIFF itself rather than delegating validation to the legacy engine or
wave.open. No model loading, subprocesses, network, ingestion or worker hooks.
Full-container 8/16/24/32-bit PCM only; reduced-valid-bit extensible PCM is outside
this contract. Silence is deliberately rejected, not asserted to be invalid WAV.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import struct
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
MAX_CHUNKS = 4096
PCM_GUID = bytes.fromhex('0100000000001000800000aa00389b71')


class AudioContractError(ValueError):
    """Stable diagnostic without input content, filenames or upstream errors."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise AudioContractError(code)


def _bounds(sample_rate, channels, min_duration, max_duration):
    for value, low, high in ((sample_rate, 8000, 192000), (channels, 1, 2)):
        _require(value is None or (type(value) is int and low <= value <= high),
                 'invalid_expectation')
    for value in (min_duration, max_duration):
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            valid = False
        _require(valid, 'invalid_duration_bounds')
    _require(0 < min_duration <= max_duration <= 3600, 'invalid_duration_bounds')


def _format(data: memoryview) -> tuple[int, int, int]:
    _require(len(data) >= 16, 'truncated_format')
    tag, channels, rate, byte_rate, alignment, bits = struct.unpack_from('<HHIIHH', data)
    _require(channels in (1, 2) and 8000 <= rate <= 192000, 'unsupported_rate_or_channels')
    _require(bits in (8, 16, 24, 32), 'unsupported_sample_width')
    width = bits // 8
    _require(alignment == channels * width, 'inconsistent_block_alignment')
    _require(byte_rate == rate * alignment, 'inconsistent_byte_rate')
    if tag == 1:
        _require(len(data) == 16 or (len(data) == 18 and data[16:18] == b'\0\0'),
                 'unsupported_pcm_extension')
    elif tag == 0xFFFE:
        _require(len(data) == 40, 'unsupported_extensible_size')
        extra, valid_bits, mask = struct.unpack_from('<HHI', data, 16)
        _require(extra == 22 and data[24:40] == PCM_GUID, 'unsupported_extensible_format')
        _require(valid_bits == bits, 'unsupported_reduced_valid_bits')
        _require(mask == 0 or mask.bit_count() == channels, 'inconsistent_channel_mask')
    else:
        raise AudioContractError('unsupported_encoding')
    return rate, channels, width


def validate_pcm(raw: bytes, *, sample_rate=None, channels=None,
                 min_duration=0.001, max_duration=300.0) -> dict:
    """Validate an immutable byte snapshot, returning source-bound PCM metrics."""
    _bounds(sample_rate, channels, min_duration, max_duration)
    _require(type(raw) is bytes, 'immutable_bytes_required')
    _require(44 <= len(raw) <= MAX_BYTES, 'invalid_size')
    _require(raw[:4] == b'RIFF' and raw[8:12] == b'WAVE', 'unsupported_container')
    _require(struct.unpack_from('<I', raw, 4)[0] == len(raw) - 8, 'riff_size_mismatch')
    view = memoryview(raw)
    cursor, chunk_count = 12, 0
    fmt, pcm = None, None
    while cursor < len(raw):
        chunk_count += 1
        _require(chunk_count <= MAX_CHUNKS, 'too_many_chunks')
        _require(len(raw) - cursor >= 8, 'truncated_chunk_header')
        kind, size = struct.unpack_from('<4sI', view, cursor)
        start = cursor + 8
        end = start + size
        padded_end = end + size % 2
        _require(padded_end <= len(raw), 'truncated_or_unpadded_chunk')
        if kind == b'fmt ':
            _require(fmt is None and pcm is None, 'ambiguous_format')
            fmt = _format(view[start:end])
        elif kind == b'data':
            _require(fmt is not None and pcm is None, 'ambiguous_data')
            pcm = view[start:end]
        cursor = padded_end
    _require(fmt is not None and pcm is not None, 'missing_format_or_data')
    rate, actual_channels, width = fmt
    _require((sample_rate is None or sample_rate == rate)
             and (channels is None or channels == actual_channels), 'unexpected_format')
    frame_size = actual_channels * width
    _require(len(pcm) > 0 and len(pcm) % frame_size == 0, 'incomplete_frames')
    frames = len(pcm) // frame_size
    duration = frames / rate
    _require(min_duration <= duration <= max_duration, 'duration_outside_bounds')
    if width == 1:
        samples = (x - 128 for x in pcm)
    elif width in (2, 4):
        samples = (x[0] for x in struct.iter_unpack('<h' if width == 2 else '<i', pcm))
    else:
        samples = (int.from_bytes(pcm[i:i + 3], 'little', signed=True)
                   for i in range(0, len(pcm), 3))
    peak, squares = 0, 0
    for value in samples:
        peak = max(peak, abs(value))
        squares += value * value
    _require(peak > 0, 'silent_pcm')
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw),
            'sample_rate': rate, 'channels': actual_channels, 'sample_width_bytes': width,
            'frames': frames, 'duration_seconds': duration, 'peak_integer': peak,
            'rms_integer': math.sqrt(squares / (frames * actual_channels)),
            'finite_samples': True, 'integrity': 'VALID_INTEGER_PCM'}


def validate_wav(path, **expectations) -> dict:
    """Read one bounded regular file without modifying it; no FIFO/device input.

Reject the final path component if it is a symlink. This is an owned local-file
API, NOT an adversarial filesystem sandbox or an authenticated upload endpoint.
"""
    target = Path(path)
    try:
        before = target.lstat()
        _require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_BYTES,
                 'bounded_regular_file_required')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(target, flags)
        try:
            with os.fdopen(fd, 'rb') as stream:
                fd = None  # The file object owns descriptor cleanup now.
                opened = os.fstat(stream.fileno())
                _require(stat.S_ISREG(opened.st_mode)
                         and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                         'input_changed')
                raw = stream.read(MAX_BYTES + 1)
                after = os.fstat(stream.fileno())
                _require((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                         == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                         and len(raw) == opened.st_size, 'input_changed')
        finally:
            if fd is not None:
                os.close(fd)
    except (OSError, OverflowError):
        raise AudioContractError('input_unavailable') from None
    return validate_pcm(raw, **expectations)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wav', type=Path, required=True)
    parser.add_argument('--sample-rate', type=int)
    parser.add_argument('--channels', type=int)
    args = parser.parse_args(argv)
    report = {'status': 'FAILED', 'scope': 'offline-integer-pcm-integrity',
              'authority': 'execution-evidence-only', 'production_accepted': False,
              'asr_tested': False, 'tts_quality_tested': False}
    try:
        report['wav'] = validate_wav(args.wav, sample_rate=args.sample_rate, channels=args.channels)
        report['status'] = 'PASSED'
    except AudioContractError as exc:
        report['reason'] = str(exc)
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report['status'] == 'PASSED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
