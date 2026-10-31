"""Preflight/contract checks for actual, opt-in operator UI Compose acceptance.

Static checks are not substitutes for running the separate Docker acceptance.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_operator_compose as probe

from qa_lab.console import check_password


class OperatorComposeContractTests(unittest.TestCase):
    def test_opt_in_required_before_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            env = dict(os.environ)
            env.pop("QA_OPERATOR_COMPOSE_OPT_IN", None)
            done = subprocess.run([sys.executable, str(ROOT / "scripts/verify_operator_compose.py"),
                                   "--output", str(path)], capture_output=True, text=True,
                                  env=env, timeout=10)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("QA_OPERATOR_COMPOSE_OPT_IN", done.stderr)
            self.assertFalse(path.exists())

    def test_output_cannot_be_inside_repository(self):
        output = ROOT / "forbidden-operator-proof.json"
        done = subprocess.run([sys.executable, str(ROOT / "scripts/verify_operator_compose.py"),
                               "--output", str(output)], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("outside the source checkout", done.stderr)
        self.assertFalse(output.exists())

    def test_remote_docker_contexts_refused_before_contact(self):
        with tempfile.TemporaryDirectory() as tmp:
            for key in ("DOCKER_HOST", "DOCKER_CONTEXT"):
                with self.subTest(key=key):
                    env = dict(os.environ, QA_OPERATOR_COMPOSE_OPT_IN="isolated-ci")
                    env.pop("DOCKER_HOST", None)
                    env.pop("DOCKER_CONTEXT", None)
                    env[key] = "tcp://untrusted.example:2375"
                    done = subprocess.run([sys.executable, str(ROOT / "scripts/verify_operator_compose.py"),
                                           "--output", str(Path(tmp) / "receipt.json")],
                                          capture_output=True, text=True, env=env, timeout=10)
                    self.assertNotEqual(done.returncode, 0)
                    self.assertIn("Remote or explicitly selected Docker contexts", done.stderr)

    def test_loopback_port_parser_denies_public_and_ambiguous_bindings(self):
        self.assertEqual(probe.localhost_port("127.0.0.1:30303"), 30303)
        for value in ("", "0.0.0.0:8780", "[::]:8780", "localhost:8780",
                      "127.0.0.1:0", "127.0.0.1:65536", "127.0.0.1:abc",
                      "127.0.0.1:3000\n127.0.0.1:3001", "127.0.0.1:90000"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                probe.localhost_port(value)

    def test_actual_browser_form_parser_is_strict(self):
        self.assertEqual(probe.parse_csrf_and_key(
            '<input name="csrf" value="' + "a" * 43 + '"><input name="idempotency_key" value="' +
            "b" * 32 + '">'), ("a" * 43, "b" * 32))
        for broken in ("", '<input name="csrf" value="short">',
                       '<input name="csrf" value="' + "a" * 43 + '">'):
            with self.subTest(broken=broken), self.assertRaises(RuntimeError):
                probe.parse_csrf_and_key(broken)

    def test_operator_password_hash_compatible_without_plaintext_serialization(self):
        encoded = probe.private_password_hash("synthetic-operator-password")
        self.assertTrue(check_password("synthetic-operator-password", encoded))
        self.assertFalse(check_password("invalid-password", encoded))
        self.assertNotIn("synthetic-operator-password", encoded)

    def test_optional_profile_no_public_access_or_internal_mount(self):
        source = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn('profiles: ["operator-ui"]', source)
        console = source.split("  operator-ui:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        self.assertIn('QA_UI_API_BASE: "http://api:8765"', console)
        self.assertIn('127.0.0.1:${QA_UI_PORT:-8780}:8780', console)
        self.assertIn('networks: [qa-edge]', console)
        self.assertIn("condition: service_healthy", console)
        for forbidden in ("qa-internal", "qa-data:/data", "QA_WORKSPACE_PATH", "/workspace"):
            self.assertNotIn(forbidden, console)
        anchor = source.split("services:\n", 1)[0]
        self.assertIn("read_only: true", anchor)
        self.assertIn('user: "10001:10001"', anchor)
        self.assertIn('127.0.0.1:${QA_API_PORT:-8765}:8765', source)
        self.assertIn("qa_lab = ['static/*.css']", (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertTrue((ROOT / "qa_lab/static/console.css").is_file())

    def test_live_runner_never_hardcodes_credentials_or_deployment(self):
        source = (ROOT / "scripts/verify_operator_compose.py").read_text(encoding="utf-8")
        self.assertIn("secrets.token_urlsafe(32)", source)
        self.assertIn("QA_OPERATOR_COMPOSE_OPT_IN", source)
        self.assertIn("Local Unix-socket Docker endpoint", source)
        self.assertIn('"down", "-v", "--remove-orphans"', source)
        for forbidden in ("railway up", "docker push", "force=True"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
