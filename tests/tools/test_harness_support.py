import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from support import portable_product_argv, resolve_jobsss_bin


class HarnessSupportTests(unittest.TestCase):
    def test_extensionless_product_launcher_uses_active_python(self):
        launcher = os.path.join(os.path.sep, "tmp", "plugin checkout", "bin", "people-finder")
        argv = portable_product_argv([launcher, "mcp", "--flag"], python_executable="python-test")
        self.assertEqual(argv, ["python-test", launcher, "mcp", "--flag"])

    def test_non_product_executable_is_preserved(self):
        argv = ["/tmp/jobsss", "mcp", "--data", "/tmp/store with spaces"]
        self.assertEqual(portable_product_argv(argv), argv)

    def test_jobsss_override_is_relative_to_plugin_root(self):
        root = os.path.abspath("/tmp/people-finder")
        resolved = resolve_jobsss_bin(root, {"JOBSSS_BIN": "../custom/jobsss"})
        self.assertEqual(resolved, os.path.abspath(os.path.join(root, "../custom/jobsss")))

    def test_jobsss_default_uses_conventional_sibling_layout(self):
        root = os.path.abspath("/tmp/people-finder")
        expected = os.path.abspath(os.path.join(root, "..", "jobsss", "bin", "jobsss"))
        self.assertEqual(resolve_jobsss_bin(root, {}), expected)


if __name__ == "__main__":
    unittest.main()
