"""Deterministic synthetic PCM fixtures; no recorded voice or external assets."""
import struct

PCM_GUID = bytes.fromhex('0100000000001000800000aa00389b71')


def chunk(kind, payload):
    return kind + struct.pack('<I', len(payload)) + payload + b'\0' * (len(payload) % 2)


def riff(*chunks):
    body = b'WAVE' + b''.join(chunks)
    return b'RIFF' + struct.pack('<I', len(body)) + body


def fixture(width=2, channels=1, rate=16000, frames=1600, *, extensible=False,
            amplitude=23, silent=False, auxiliary=False):
    value = 0 if silent else amplitude
    sample = bytes([128 + value]) if width == 1 else value.to_bytes(width, 'little', signed=True)
    pcm = sample * frames * channels
    fmt = struct.pack('<HHIIHH', 0xFFFE if extensible else 1, channels, rate,
                      rate * channels * width, channels * width, width * 8)
    if extensible:
        fmt += struct.pack('<HHI', 22, width * 8, 0) + PCM_GUID
    extras = [chunk(b'JUNK', b'odd')] if auxiliary else []
    return riff(chunk(b'fmt ', fmt), *extras, chunk(b'data', pcm))


def change(raw, offset, pattern, value):
    result = bytearray(raw)
    struct.pack_into(pattern, result, offset, value)
    return bytes(result)


def cases():
    """Input bytes and expectations only; never executes either validator."""
    result = []

    def add(name, raw, accepted, *, group='compatibility', **options):
        result.append({'id': name, 'raw': raw, 'options': options,
                       'accepted': accepted, 'group': group})

    for width in (1, 2, 3, 4):
        for channels in (1, 2):
            for ext in (False, True):
                add(f'pcm-{width * 8}bit-{channels}ch-' + ('ext' if ext else 'basic'),
                    fixture(width, channels, extensible=ext), True)
    raw = fixture()
    add('padded-ancillary-chunk', fixture(auxiliary=True), True)
    add('padded-odd-data', fixture(width=1, frames=1601), True)
    add('rate-8000', fixture(rate=8000), True)
    add('rate-192000', fixture(rate=192000), True)
    add('negative-signed-24bit', fixture(width=3, amplitude=-8388608), True)
    add('negative-signed-32bit', fixture(width=4, amplitude=-2147483648), True)
    add('pcm-format-18byte', riff(chunk(b'fmt ', raw[20:36] + b'\0\0'),
                                  chunk(b'data', raw[44:])), True)
    add('matching-expectations', raw, True, sample_rate=16000, channels=1)
    add('duration-exact-bounds', raw, True, min_duration=0.1, max_duration=0.1)
    add('not-wave', b'not a WAV file', False)
    add('empty', b'', False)
    add('truncated-data', raw[:-1], False)
    add('trailing-bytes', raw + b'x', False)
    add('silent-unsigned', fixture(width=1, silent=True), False)
    add('silent-signed', fixture(silent=True), False)
    add('no-data', riff(chunk(b'fmt ', raw[20:36])), False)
    add('data-before-format', riff(chunk(b'data', raw[44:]), chunk(b'fmt ', raw[20:36])), False)
    add('duplicate-data', riff(chunk(b'fmt ', raw[20:36]), chunk(b'data', raw[44:]),
                               chunk(b'data', raw[44:])), False)
    add('incomplete-frame', riff(chunk(b'fmt ', raw[20:36]), chunk(b'data', b'\1\0\1')), False)
    add('empty-data', fixture(frames=0), False)
    add('unsupported-float', change(raw, 20, '<H', 3), False)
    add('wrong-rate-expectation', raw, False, sample_rate=8000)
    add('wrong-channel-expectation', raw, False, channels=2)
    add('too-short-policy', raw, False, min_duration=0.2)
    add('too-long-policy', raw, False, max_duration=0.05)
    # Deliberate stronger validation, not silently counted as parity successes.
    add('bad-block-alignment', change(raw, 32, '<H', 1), False, group='hardening')
    add('bad-byte-rate', change(raw, 28, '<I', 7), False, group='hardening')
    add('15bit-container', change(raw, 34, '<H', 15), False, group='hardening')
    add('duplicate-format', riff(chunk(b'fmt ', raw[20:36]), chunk(b'fmt ', raw[20:36]),
                                 chunk(b'data', raw[44:])), False, group='hardening')
    ext = fixture(extensible=True)
    add('reduced-valid-bits', change(ext, 38, '<H', 12), False, group='unsupported')
    add('wrong-channel-mask', change(ext, 40, '<I', 3), False, group='hardening')
    return result
