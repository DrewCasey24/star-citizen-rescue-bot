"""Environment guardrails for staging and destructive operational tests."""

import os


def environment_name():
    return os.getenv("APP_ENV", "production").strip().lower() or "production"


def destructive_smoke_tests_allowed():
    return environment_name() not in {"production", "prod"} and os.getenv(
        "ALLOW_DESTRUCTIVE_SMOKE_TESTS", "false"
    ).strip().lower() in {"1", "true", "yes"}


def require_destructive_smoke_test_permission():
    if not destructive_smoke_tests_allowed():
        raise RuntimeError(
            "Destructive smoke tests are blocked. Use a non-production APP_ENV and explicitly set ALLOW_DESTRUCTIVE_SMOKE_TESTS=true."
        )
