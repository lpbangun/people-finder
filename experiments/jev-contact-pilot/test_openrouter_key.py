import importlib.util
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


path = Path(__file__).with_name("openrouter_key.py")
spec = importlib.util.spec_from_file_location("openrouter_key", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OpenRouterKeyTests(unittest.TestCase):
    def test_save_and_reject_loose_permissions(self):
        with TemporaryDirectory() as directory:
            key_path = Path(directory) / "config" / "openrouter.key"
            with patch.object(module.getpass, "getpass", return_value="test-key"):
                module.save_key(key_path)
            self.assertEqual(module.read_key(key_path), "test-key")
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
            os.chmod(key_path, 0o644)
            with self.assertRaises(ValueError):
                module.read_key(key_path)


if __name__ == "__main__":
    unittest.main()
