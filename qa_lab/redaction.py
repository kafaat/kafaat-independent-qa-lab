"""Bounded secret redaction for receipt output tails, applied before hashing.

The rule set is a reduced port of ``src/portable_ci_lab/redaction.py``
(rules ``ull-redaction-2``) from kafaat/unified-local-lab: the specific
token rules and PEM private keys only. Two deliberate differences:

* no Shannon-entropy rule, so test output such as digests, base64 fixtures
  or identifiers is not mangled;
* placeholders carry no digest of the secret (``[REDACTED:<rule>]``), so a
  receipt cannot be used to confirm a guessed low-entropy password.

Redaction is heuristic. A receipt with zero findings is not evidence that
its output contains no secrets (``completeness_claimed`` stays false).
Every quantifier is bounded, and input is capped before scanning.
"""
import bisect
import re

RULES_VERSION = "qa-redaction-1 (from ull-redaction-2, specific rules only)"
MAX_INPUT_CHARS = 65536

_B = r"(?<![A-Za-z0-9_-])"
_E = r"(?![A-Za-z0-9_-])"

# (rule, pattern, groups holding the secret). Order breaks ties for equal spans.
_RULES = (
    ("aws-access-key-id", r"(?<![A-Za-z0-9])((?:AKIA|ASIA|ABIA|ACCA|A3T[A-Z0-9])[A-Z0-9]{16})(?![A-Za-z0-9])", (1,)),
    ("github-token", _B + r"(gh[pousr]_[A-Za-z0-9]{36,255})" + _E, (1,)),
    ("github-fine-grained-pat", _B + r"(github_pat_[A-Za-z0-9_]{22,255})" + _E, (1,)),
    ("slack-token", _B + r"(xox[abposre]-[A-Za-z0-9-]{10,250})" + _E, (1,)),
    ("slack-webhook", r"(?<![A-Za-z0-9])(https://hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9_/+-]{20,300})", (1,)),
    ("anthropic-api-key", _B + r"(sk-ant-[A-Za-z0-9_-]{20,300})" + _E, (1,)),
    ("openai-api-key", _B + r"(sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,300})" + _E, (1,)),
    ("jwt", _B + r"(eyJ[A-Za-z0-9_-]{5,4096}\.eyJ[A-Za-z0-9_-]{5,8192}\.[A-Za-z0-9_-]{0,4096})", (1,)),
    ("url-credentials", r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s:/@?#]{0,256}:([^\s/@?#]{1,256})@", (1,)),
    ("bearer-token", r"(?i)(?<![A-Za-z0-9])bearer[ \t]{1,4}([A-Za-z0-9._~+/=-]{12,4096})", (1,)),
    ("basic-auth", r"(?i)(?<![A-Za-z0-9])basic[ \t]{1,4}([A-Za-z0-9+/]{8,4096}={0,2})(?![A-Za-z0-9+/=])", (1,)),
    ("keyword-assignment",
     r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|auth[_-]?key|private[_-]?key|credentials?)"
     r"[A-Za-z0-9_.-]{0,32}[\"']?[ \t]{0,4}(?::|=)[ \t]{0,4}"
     r"(?:\"([^\"\r\n]{1,4096})\"|'([^'\r\n]{1,4096})'|([^\s\"'`,;&]{4,4096}))", (1, 2, 3)),
)
_COMPILED = tuple((name, re.compile(pattern), groups) for name, pattern, groups in _RULES)
_ORDER = {name: index for index, (name, _, _) in enumerate(_RULES)}
_PEM_BEGIN = re.compile(r"-----BEGIN[ A-Z0-9]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"-----END[ A-Z0-9]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PEM_WINDOW = 65536
_PLACEHOLDER = re.compile(r"\[REDACTED:[a-z0-9-]{1,64}\]")
_PLACEHOLDER_VALUES = frozenset({
    "none", "null", "nil", "true", "false", "undefined", "redacted", "masked", "changeme", "example",
    "required", "missing", "invalid", "expired", "empty", "unset", "string", "xxxx", "****"})


def _keyword_value_ok(value):
    lowered = value.lower()
    return not (lowered in _PLACEHOLDER_VALUES or value.isdigit()
                or value.startswith(("$", "<", "%(", "{{", "[REDACTED"))
                or set(value) <= {"*", "x", "X", ".", "-"})


def _candidates(text):
    for name, pattern, groups in _COMPILED:
        for match in pattern.finditer(text):
            index = next((i for i in groups if match.group(i) is not None), None)
            if index is None:
                continue
            value = match.group(index)
            if name == "keyword-assignment" and not _keyword_value_ok(value.strip()):
                continue
            if name == "bearer-token" and value.isalpha():
                continue
            start, end = match.span(index)
            yield start, end, name
    ends = [m.end() for m in _PEM_END.finditer(text)]
    covered = -1
    for match in _PEM_BEGIN.finditer(text):
        if match.start() < covered:
            continue
        index = bisect.bisect_left(ends, match.end())
        if index < len(ends) and ends[index] - match.start() <= _PEM_WINDOW:
            end = ends[index]
        else:
            blank = text.find("\n\n", match.end(), match.end() + _PEM_WINDOW)
            end = blank if blank >= 0 else min(len(text), match.end() + _PEM_WINDOW)
        covered = end
        yield match.start(), end, "private-key"


def redact_text(text):
    """Return (redacted_text, {rule: count}). Input must be at most MAX_INPUT_CHARS."""
    if type(text) is not str:
        raise TypeError("text required")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError("redaction input limit")
    blocked = [m.span() for m in _PLACEHOLDER.finditer(text)]
    starts = [a for a, _ in blocked]
    chosen, last = [], -1
    for start, end, name in sorted(_candidates(text),
                                   key=lambda c: (c[0], -(c[1] - c[0]), _ORDER.get(c[2], len(_ORDER)))):
        if end <= start or start < last:
            continue
        i = bisect.bisect_right(starts, start)
        if (i and blocked[i - 1][1] > start) or (i < len(starts) and starts[i] < end):
            continue
        chosen.append((start, end, name))
        last = end
    parts, counts, cursor = [], {}, 0
    for start, end, name in chosen:
        parts.append(text[cursor:start])
        parts.append(f"[REDACTED:{name}]")
        counts[name] = counts.get(name, 0) + 1
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts), counts


def redact_tail(output, keep):
    """Redact the last MAX_INPUT_CHARS of output, then keep its last ``keep`` characters.

    Redacting before truncating means a secret is never cut in half at the
    receipt boundary and left partly visible.
    """
    if keep > MAX_INPUT_CHARS:
        raise ValueError("keep exceeds redaction window")
    redacted, counts = redact_text((output or "")[-MAX_INPUT_CHARS:])
    return redacted[-keep:] if keep else "", counts


def summary(*counts):
    """Receipt metadata for one or more redaction passes; never contains secret values."""
    merged = {}
    for item in counts:
        for name, number in item.items():
            merged[name] = merged.get(name, 0) + number
    return {"rules_version": RULES_VERSION, "findings": dict(sorted(merged.items())),
            "findings_count": sum(merged.values()), "completeness_claimed": False}
