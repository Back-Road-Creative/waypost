"""The daemon stores a title-length range together with what backs it.

With no usable history the optimizer returns the editorial style range; the
daemon used to store and report that as the "optimal" length as if measured.
"""

import json
from types import SimpleNamespace

import pytest

from seo_manager.analytics.continuous_optimization_daemon import (
    ContinuousOptimizationDaemon,
)


def _daemon(tmp_path, lo=50, hi=70, basis="style"):
    guidance = SimpleNamespace(range=(lo, hi), basis=basis)
    manager = SimpleNamespace(
        optimizer=SimpleNamespace(
            analyze_patterns=lambda min_views=0: {"tags": []},
            get_title_length_guidance=lambda: guidance,
        ),
        analytics=SimpleNamespace(get_summary=lambda: {}),
    )
    daemon = ContinuousOptimizationDaemon(manager=manager, state_dir=tmp_path / "state")
    return daemon, guidance


@pytest.mark.parametrize(
    ("basis", "lo", "hi"), [("style", 50, 70), ("measured", 42, 62)]
)
def test_the_basis_is_stored_and_reported_with_the_range(tmp_path, basis, lo, hi):
    daemon, _ = _daemon(tmp_path, lo, hi, basis)
    result = daemon._analyze_patterns()
    assert result.success
    assert result.metrics["title_length_basis"] == basis
    assert daemon.state.title_length_basis == basis
    assert (
        daemon.state.optimal_title_length_min,
        daemon.state.optimal_title_length_max,
    ) == (lo, hi)
    assert daemon._generate_template_config()["title"]["basis"] == basis
    assert daemon.get_status()["title_length_basis"] == basis


def test_a_basis_change_alone_is_a_change(tmp_path):
    daemon, guidance = _daemon(tmp_path, basis="style")
    daemon._analyze_patterns()
    guidance.basis = "measured"
    result = daemon._analyze_patterns()
    assert any(c.startswith("updated_title_length") for c in result.changes_made)


def test_a_state_saved_before_the_basis_existed_reads_back_unknown(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "daemon_state.json").write_text(
        json.dumps({"optimal_title_length_min": 55, "optimal_title_length_max": 65})
    )
    daemon, _ = _daemon(tmp_path)
    assert daemon.state.optimal_title_length_min == 55
    assert daemon.state.title_length_basis is None  # not recorded, never "style"
    assert daemon._generate_template_config()["title"]["basis"] is None
