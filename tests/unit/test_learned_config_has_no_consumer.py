"""The applied-settings path has no consumer, and says so.

A review would ask for a demonstration that a generator reads what
`RecommendationAutoApplier` applies -- that a next draft comes out different,
for the cited reason. No such demonstration exists, because no such consumer
exists. `_apply_config_value` writes into `config_dir/learned_config.json`, and
the only code in this package that reads those keys back is `_get_config_value`
on the applier itself, which exists so `rollback_recommendation` can restore an
old value. Nothing in `generation/` or `platforms/` opens the file; nothing in
the wider code base that embeds this package does either.

So there is no "the next draft changed for this reason" test to write. Writing
one anyway -- against a fixture that pretends a generator consults the file --
would be the exact defect this test guards against: an applied-settings path that
claims to be proven.

What IS testable is the absence itself. These tests pin it so the module
docstring fails loudly the day someone wires a real consumer, instead of
quietly becoming false.
"""

import inspect
from pathlib import Path

from seo_manager.analytics import recommendation_auto_applier as raa
from seo_manager.analytics.recommendation_auto_applier import RecommendationAutoApplier

#: Every key `_apply_config_change` can land a value on, read off the call
#: sites rather than retyped, so a new one cannot slip past this test.
APPLIED_CONFIG_KEYS = frozenset(
    {
        "seo.title.optimal_length",
        "seo.tags.recommended",
        "thumbnails.default_style",
        "descriptions.default_template",
        "posting.default_time",
    }
)

#: The package root, located from the module under test so the test works from
#: any cwd and against an installed copy.
_PACKAGE_ROOT = Path(raa.__file__).resolve().parent.parent

#: The two modules allowed to name these keys: the applier writes and rolls
#: them back, and the daemon names its own differently-shaped sections.
_WRITER = "recommendation_auto_applier.py"


def _package_sources():
    return sorted(p for p in _PACKAGE_ROOT.rglob("*.py") if p.name != "__init__.py")


class TestTheAppliedKeysAreWrittenNowhereElse:
    def test_every_documented_key_is_actually_a_call_site(self):
        """The list above is only meaningful if it is complete."""
        source = inspect.getsource(RecommendationAutoApplier)
        found = {key for key in APPLIED_CONFIG_KEYS if f'config_key="{key}"' in source}

        assert found == APPLIED_CONFIG_KEYS

    def test_no_generator_or_platform_module_reads_an_applied_key(self):
        offenders = {}

        for path in _package_sources():
            if path.name == _WRITER:
                continue
            text = path.read_text()
            hits = sorted(key for key in APPLIED_CONFIG_KEYS if key in text)
            if hits:
                offenders[str(path.relative_to(_PACKAGE_ROOT))] = hits

        assert offenders == {}, (
            "a consumer of the applied settings now exists: update the "
            "`recommendation_auto_applier` module docstring, which currently "
            "states there is none, and add the test that shows a draft "
            f"changing for the cited reason. Found: {offenders}"
        )

    def test_nothing_outside_the_analytics_package_opens_learned_config(self):
        offenders = [
            str(path.relative_to(_PACKAGE_ROOT))
            for path in _package_sources()
            if path.parent.name != "analytics" and "learned_config" in path.read_text()
        ]

        assert offenders == [], (
            f"learned_config.json gained a reader outside analytics/: {offenders}"
        )


class TestTheDocstringStatesTheAbsence:
    def test_the_module_says_plainly_that_nothing_reads_what_it_applies(self):
        """Where no consumer exists, say so rather than
        implying one by omission."""
        doc = raa.__doc__ or ""

        assert "Nothing reads what this module applies" in doc
        assert "_get_config_value" in doc, (
            "the docstring must name the sole reader, so the claim is checkable"
        )

    def test_every_reader_of_an_applied_key_is_inside_the_applier(self):
        """Both readers are self-reads, not consumption.

        `_apply_config_change` captures the old value so `rollback_recommendation`
        can restore it; `_apply_tag_rec` merges with the tag list this same
        class wrote last time. Neither turns a stored setting into a draft --
        the file is a ledger the applier keeps about itself.
        """
        callers = {
            name
            for name, member in vars(RecommendationAutoApplier).items()
            if callable(member)
            and name != "_get_config_value"
            and "_get_config_value" in inspect.getsource(member)
        }

        assert callers == {"_apply_config_change", "_apply_tag_rec"}
