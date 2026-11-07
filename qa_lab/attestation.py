"""Receipts as in-toto Statements in DSSE envelopes, optionally Ed25519-signed.

Shared contract with kafaat/unified-local-lab (``portable_ci_lab.attestation``
and ``portable_ci_lab.signing``): the same Statement and predicate types,
payload type, DSSE v1 PAE, envelope keys (``authentication_claimed`` is always
false in the envelope itself) and trust-file lines ``principal ed25519
<base64 SubjectPublicKeyInfo DER>``. Only the Ed25519/openssl backend is
implemented here; nothing is copied wholesale.

A receipt's own ``sha256`` gives consistency, not authenticity. A signature
means something only when a verifier checks it against a key listed for a
principal in a trust file the verifier chose; a valid signature by an unlisted
key is UNTRUSTED. A missing ``openssl`` is BLOCKED, never a pass. Key material
and tool stderr are never copied into results.
"""
import argparse
import base64
import binascii
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .core import Lab, digest

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://in-toto.io/attestation/test-result/v0.1"
PAYLOAD_TYPE = "application/vnd.in-toto+json"
MAX_ENVELOPE_BYTES = 1024 * 1024
MAX_TRUST_BYTES = 64 * 1024
TOOL_TIMEOUT = 30
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}\Z")
# Ed25519 SubjectPublicKeyInfo DER = this 12-byte prefix + 32-byte key.
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


class AttestationError(ValueError):
    """Input refused before an attestation could be built or checked."""


def pae(payload_type, payload):
    """DSSE v1 Pre-Authentication Encoding: ``DSSEv1 SP LEN SP type SP LEN SP body``."""
    if not isinstance(payload_type, str) or not isinstance(payload, (bytes, bytearray)):
        raise AttestationError("PAE requires a str type and bytes payload")
    encoded = payload_type.encode("utf-8")
    return b"DSSEv1 %d %s %d %s" % (len(encoded), encoded, len(payload), bytes(payload))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def receipt_statement(receipt):
    """in-toto Statement for one QA receipt; the subject is the receipt's own digest."""
    if not isinstance(receipt, dict) or not _HEX64.match(str(receipt.get("sha256", ""))):
        raise AttestationError("receipt with a sha256 digest required")
    if digest({k: v for k, v in receipt.items() if k != "sha256"}) != receipt["sha256"]:
        raise AttestationError("receipt digest does not match its content")
    status = receipt.get("status")
    recipe = str(receipt.get("recipe", ""))
    return {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": f"qa-lab/receipt/{receipt.get('run_id', '')}",
                     "digest": {"sha256": receipt["sha256"]}}],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "result": "PASSED" if status == "PASSED" else "FAILED",
            "configuration": [{
                "name": f"qa-lab/recipe/{recipe}",
                "annotations": {
                    "digest_of": "canonical receipt JSON without its sha256 field",
                    "receipt_status": status, "project": receipt.get("project"),
                    "source_sha": receipt.get("source_sha"),
                    "source_sha_verified": receipt.get("source_sha_verified") is True,
                    "environment_claim": receipt.get("environment_claim"),
                    "producer": "qa_lab.attestation", "authentication_claimed": False,
                    "production_accepted": False}}],
            "passedTests": [recipe] if status == "PASSED" else [],
            "warnedTests": [],
            "failedTests": [] if status == "PASSED" else [recipe],
        },
    }


def _tool():
    return shutil.which("openssl")


def _run(args, timeout=TOOL_TIMEOUT):
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False)


def _ed25519_spki(der):
    if len(der) != 44 or not der.startswith(_ED25519_SPKI_PREFIX):
        raise AttestationError("not an Ed25519 SubjectPublicKeyInfo")
    return der


def keyid_for(spki_der):
    return "sha256:" + hashlib.sha256(spki_der).hexdigest()


class Ed25519Signer:
    """Signs DSSE PAE bytes with an Ed25519 private key via ``openssl pkeyutl``."""

    def __init__(self, key_path):
        self.key_path = str(Path(key_path).resolve(strict=True))
        tool = _tool()
        if not tool:
            raise AttestationError("BLOCKED: openssl not found")
        self.tool = tool
        out = _run([tool, "pkey", "-in", self.key_path, "-passin", "pass:", "-pubout", "-outform", "DER"])
        if out.returncode != 0:
            raise AttestationError("cannot read Ed25519 private key")
        self.spki = _ed25519_spki(out.stdout)

    @property
    def keyid(self):
        return keyid_for(self.spki)

    def sign(self, pae_bytes):
        with tempfile.TemporaryDirectory() as work:
            message = Path(work, "pae.bin")
            message.write_bytes(pae_bytes)
            out = _run([self.tool, "pkeyutl", "-sign", "-rawin", "-inkey", self.key_path,
                        "-passin", "pass:", "-in", str(message)])
        if out.returncode != 0 or len(out.stdout) != 64:
            raise AttestationError("Ed25519 signing failed")
        return out.stdout


def envelope(statement, signer=None):
    """DSSE envelope; unsigned unless ``signer`` is given. Never claims authentication."""
    payload = _canonical(statement)
    signatures = []
    if signer is not None:
        signatures.append({"keyid": signer.keyid,
                           "sig": base64.b64encode(signer.sign(pae(PAYLOAD_TYPE, payload))).decode("ascii")})
    return {"payloadType": PAYLOAD_TYPE, "payload": base64.b64encode(payload).decode("ascii"),
            "signatures": signatures, "authentication_claimed": False}


def load_trust_file(path):
    """Parse ``principal ed25519 <base64 SPKI DER>`` lines; any bad line refuses the file."""
    raw = Path(path).read_bytes()
    if len(raw) > MAX_TRUST_BYTES:
        raise AttestationError("trust file too large")
    entries, keys = {}, set()
    for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) != 3 or fields[1] != "ed25519" or not _PRINCIPAL.match(fields[0]):
            raise AttestationError(f"trust file line {number}: expected 'principal ed25519 <base64>'")
        try:
            der = _ed25519_spki(base64.b64decode(fields[2], validate=True))
        except (binascii.Error, ValueError) as error:
            raise AttestationError(f"trust file line {number}: bad key") from error
        if der in keys:
            raise AttestationError(f"trust file line {number}: duplicate key")
        keys.add(der)
        entries[keyid_for(der)] = {"principal": fields[0], "spki": der, "line": number}
    if not entries:
        raise AttestationError("trust file lists no keys")
    return entries


def _verify_sig(tool, spki, pae_bytes, signature):
    pem = (b"-----BEGIN PUBLIC KEY-----\n" + base64.encodebytes(spki) + b"-----END PUBLIC KEY-----\n")
    with tempfile.TemporaryDirectory() as work:
        paths = {name: Path(work, name) for name in ("key.pem", "pae.bin", "sig.bin")}
        paths["key.pem"].write_bytes(pem)
        paths["pae.bin"].write_bytes(pae_bytes)
        paths["sig.bin"].write_bytes(signature)
        out = _run([tool, "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(paths["key.pem"]),
                    "-sigfile", str(paths["sig.bin"]), "-in", str(paths["pae.bin"])])
    return out.returncode == 0 and b"Signature Verified Successfully" in out.stdout


def _strict_b64(text):
    if not isinstance(text, str):
        raise AttestationError("base64 text required")
    return base64.b64decode(text, validate=True)


def _report(status, reason=None, **extra):
    return {"status": status, "reason": reason, "authentication_claimed": status == "TRUSTED", **extra}


def verify_envelope(document, receipt, trust=None):
    """Check structure, PAE signatures (against ``trust`` entries) and the receipt binding.

    Status: TRUSTED (a valid signature by a listed key), UNSIGNED (consistent,
    no signatures), UNTRUSTED (signed, but by no listed key, or no trust given),
    INVALID (malformed or a bad signature), MISMATCH (does not describe this
    receipt), BLOCKED (openssl missing).
    """
    try:
        if not isinstance(document, dict) or set(document) - {"payloadType", "payload", "signatures",
                                                               "authentication_claimed"} \
                or not {"payloadType", "payload", "signatures"} <= set(document):
            return _report("INVALID", "ENVELOPE_KEYS")
        if document.get("authentication_claimed", False) is not False:
            return _report("INVALID", "ENVELOPE_CLAIMS_AUTHENTICATION")
        if document["payloadType"] != PAYLOAD_TYPE:
            return _report("INVALID", "WRONG_PAYLOAD_TYPE")
        payload = _strict_b64(document["payload"])
        signatures = document["signatures"]
        if not isinstance(signatures, list) or len(signatures) > 16:
            return _report("INVALID", "SIGNATURES")
        decoded = []
        for item in signatures:
            if not isinstance(item, dict) or set(item) != {"keyid", "sig"} or not isinstance(item["keyid"], str):
                return _report("INVALID", "SIGNATURE_ENTRY")
            decoded.append((item["keyid"], _strict_b64(item["sig"])))
    except (AttestationError, binascii.Error, ValueError):
        return _report("INVALID", "BASE64")
    pae_bytes = pae(PAYLOAD_TYPE, payload)
    trusted_principal = None
    if decoded:
        listed = [(keyid, sig) for keyid, sig in decoded if trust and keyid in trust]
        if listed:
            tool = _tool()
            if not tool:
                return _report("BLOCKED", "OPENSSL_NOT_FOUND")
            for keyid, sig in listed:
                if not _verify_sig(tool, trust[keyid]["spki"], pae_bytes, sig):
                    return _report("INVALID", "SIGNATURE_NOT_VERIFIED", keyid=keyid)
                trusted_principal = trusted_principal or trust[keyid]["principal"]
    try:
        statement = json.loads(payload.decode("utf-8"))
        expected = receipt_statement(receipt)
    except (UnicodeError, ValueError, AttestationError):
        return _report("MISMATCH", "RECEIPT_OR_STATEMENT_INVALID")
    if statement != expected:
        return _report("MISMATCH", "STATEMENT_DOES_NOT_DESCRIBE_RECEIPT")
    if trusted_principal:
        return _report("TRUSTED", None, principal=trusted_principal,
                       authentication_scope="key listed for principal in the supplied trust file")
    if decoded:
        return _report("UNTRUSTED", "SIGNING_KEY_NOT_IN_TRUST_FILE")
    return _report("UNSIGNED", None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export or verify a QA receipt DSSE envelope.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--data-dir", required=True)
        cmd.add_argument("--run-id", required=True)
    sub.choices["export"].add_argument("--key", help="Ed25519 private key (PEM) to sign with")
    sub.choices["export"].add_argument("--output", required=True)
    sub.choices["verify"].add_argument("--envelope", required=True)
    sub.choices["verify"].add_argument("--trust", help="trust file of 'principal ed25519 <base64>' lines")
    args = parser.parse_args(argv)
    receipt = Lab(args.data_dir).get(args.run_id)["receipt"]
    try:
        if args.command == "export":
            signer = Ed25519Signer(args.key) if args.key else None
            Path(args.output).write_text(json.dumps(envelope(receipt_statement(receipt), signer),
                                                    indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": "WRITTEN", "signed": signer is not None}))
            return 0
        raw = Path(args.envelope).read_bytes()
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise AttestationError("envelope too large")
        trust = load_trust_file(args.trust) if args.trust else None
        result = verify_envelope(json.loads(raw), receipt, trust)
    except (AttestationError, OSError, ValueError) as error:
        print(json.dumps({"status": "INVALID", "reason": str(error)}))
        return 1
    print(json.dumps(result))
    return 0 if result["status"] in ("TRUSTED", "UNSIGNED") else 1


if __name__ == "__main__":
    sys.exit(main())
