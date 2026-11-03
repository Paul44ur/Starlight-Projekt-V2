"""The tests' own safety net (tests/conftest.py): an environment variable that one test
changes directly (not through monkeypatch) is back as it was for the next test. These two
run in this order (pytest keeps a file's order)."""

from __future__ import annotations

import os

PROBE = "STARLIGHT_TEST_LEAK_PROBE"


def test_a_test_changes_the_environment_directly() -> None:
    assert PROBE not in os.environ
    os.environ[PROBE] = "leaked"  # (as the launcher's load_settings does with UV_CACHE_DIR)
    os.environ.pop("STARLIGHT_HOME", None)  # (and one that may be there, taken away)


def test_the_next_test_does_not_see_it() -> None:
    assert PROBE not in os.environ
