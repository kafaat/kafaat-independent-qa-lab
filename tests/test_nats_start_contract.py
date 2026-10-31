import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("contract", Path(__file__).resolve().parents[1] / "scripts" / "check_nats_contract.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
DOCKER = 'FROM nats:2.10-alpine\nENTRYPOINT ["nats-server"]\nCMD ["-js", "-sd", "/data"]\n'


class TestNatsContract(unittest.TestCase):
    def test_valid_image_contract(self):
        self.assertEqual(mod.verify(DOCKER, None), [])

    def test_custom_railway_start_rejected(self):
        self.assertIn("RAILWAY_START_OVERRIDE_MUST_BE_UNSET", mod.verify(DOCKER, "nats-server -js -sd /data"))

    def test_corrupt_image_contract_rejected(self):
        self.assertIn("NATS_DOCKER_CONTRACT_CHANGED", mod.verify(DOCKER.replace('"-js"', '"--help"'), None))

    def test_missing_entrypoint_rejected(self):
        self.assertIn("NATS_ENTRYPOINT_CMD_REQUIRED", mod.verify('CMD ["-js"]', None))


if __name__ == "__main__":
    unittest.main()
