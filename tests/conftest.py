"""Test-run configuration shared by every test in this tree."""

from pathlib import Path

from hypothesis import configuration

# Hypothesis writes its example database into a directory of its own beside the
# tests. Keep it inside pytest's cache directory instead, so a test run adds no
# new top-level directory to the checkout.
configuration.set_hypothesis_home_dir(
    Path(__file__).resolve().parents[1] / ".pytest_cache" / "hypothesis"
)
