import os
import unittest
from unittest.mock import patch

import environment_guard


class EnvironmentGuardTests(unittest.TestCase):
    def test_production_blocks_destructive_smoke_tests(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "ALLOW_DESTRUCTIVE_SMOKE_TESTS": "true"}, clear=False):
            self.assertFalse(environment_guard.destructive_smoke_tests_allowed())
            with self.assertRaises(RuntimeError):
                environment_guard.require_destructive_smoke_test_permission()

    def test_staging_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {"APP_ENV": "staging", "ALLOW_DESTRUCTIVE_SMOKE_TESTS": "false"}, clear=False):
            self.assertFalse(environment_guard.destructive_smoke_tests_allowed())
        with patch.dict(os.environ, {"APP_ENV": "staging", "ALLOW_DESTRUCTIVE_SMOKE_TESTS": "true"}, clear=False):
            self.assertTrue(environment_guard.destructive_smoke_tests_allowed())


if __name__ == "__main__":
    unittest.main()
