"""The applied-settings writer is retired, and stays retired.

`RecommendationAutoApplier` wrote five settings into
`config_dir/learned_config.json` -- `seo.title.optimal_length`,
`seo.tags.recommended`, `thumbnails.default_style`,
`descriptions.default_template` and `posting.default_time` -- and nothing ever
read them back except the applier itself. The one key a consumer was proposed
for, the title length, could not be wired honestly:

* it is a ``{min, max}`` range around the top quarter's average length, with no
  sample size, no window and no Short/longform split;
* with no data it is an invented ``(50, 70)``, stored exactly like a measurement;
* `select_title`'s ``max_title_chars`` excludes a candidate outright, so a cap
  near 70 would drop fact-complete place titles, and a minimum invites padding.

So the writer was removed rather than given a consumer that would always have
to fall back. These tests pin the absence: no module in the package writes or
reads any of the five keys, the applier and the daemon's apply step are gone,
and title selection runs on its own `DEFAULT_POLICY`.

An admissible per-format title-length measurement (sample size, window, no
invented defaults) is separate future work; it would arrive as new code with
its own tests, not by reviving this file.
"""

import importlib
import inspect
from pathlib import Path

import pytest

from seo_manager.analytics import continuous_optimization_daemon as cod
from seo_manager.analytics import title_selection

#: Every key the retired applier landed a value on.
RETIRED_CONFIG_KEYS = frozenset(
    {
        "seo.title.optimal_length",
        "seo.tags.recommended",
        "thumbnails.default_style",
        "descriptions.default_template",
        "posting.default_time",
    }
)

#: The package root, located from a module under test so the test works from
#: any cwd and against an installed copy.
_PACKAGE_ROOT = Path(cod.__file__).resolve().parent.parent


def _package_sources():
    return sorted(_PACKAGE_ROOT.rglob("*.py"))


class TestTheWriterIsGone:
    def test_the_applier_module_no_longer_exists(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("seo_manager.analytics.recommendation_auto_applier")

    def test_the_package_exports_no_applier(self):
        analytics = importlib.import_module("seo_manager.analytics")
        for name in (
            "RecommendationAutoApplier",
            "AutoApplierConfig",
            "AppliedRecommendation",
        ):
            assert not hasattr(analytics, name), name
            assert name not in analytics.__all__, name

    def test_no_module_names_a_retired_key(self):
        offenders = {}
        for path in _package_sources():
            hits = sorted(k for k in RETIRED_CONFIG_KEYS if k in path.read_text())
            if hits:
                offenders[str(path.relative_to(_PACKAGE_ROOT))] = hits

        assert offenders == {}, f"a retired learned-config key is back: {offenders}"

    def test_the_daemon_cli_has_no_apply_step_and_no_config_dir(self):
        assert not hasattr(cod, "_apply_learned")
        source = inspect.getsource(cod.main)
        assert "--config-dir" not in source
        assert "Applier" not in inspect.getsource(cod)

    def test_nothing_outside_the_daemon_opens_learned_config(self):
        """The daemon keeps its own learned-state snapshot under ``state_dir``
        for change detection; no other module reads or writes that name."""
        offenders = [
            str(path.relative_to(_PACKAGE_ROOT))
            for path in _package_sources()
            if path.name != "continuous_optimization_daemon.py"
            and "learned_config" in path.read_text()
        ]

        assert offenders == [], f"learned_config.json gained a reader: {offenders}"


class TestTitleSelectionRunsOnItsOwnPolicy:
    def test_the_default_cap_is_the_platform_limit_not_a_learned_length(self):
        assert title_selection.DEFAULT_POLICY.max_title_chars == 100

    def test_title_selection_reads_no_learned_setting(self):
        source = inspect.getsource(title_selection)
        for marker in ("learned_config", "optimal_length", "seo_optimizer"):
            assert marker not in source, marker
