"""Every CLI subcommand at least builds and answers ``--help``.

``seo-manager``'s parser is flat (``report``) except ``schedule``, whose
own actions are discovered too, so a bad ``type=``/default/import is caught the
same way as in the other services. Discovery walks ``parser._actions`` for ``argparse._SubParsersAction`` and
reads ``.choices``, the same mechanism the video pipeline's CLI test uses.
"""

import argparse
import subprocess
import sys

import pytest

from seo_manager.cli import build_parser


def _discover_subcommands() -> list[str]:
    names: list[str] = []
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.extend(action.choices.keys())
    return names


def _discover_schedule_actions() -> list[str]:
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            sched = action.choices["schedule"]
            for inner in sched._actions:
                if isinstance(inner, argparse._SubParsersAction):
                    return list(inner.choices)
    return []


SUBCOMMAND_NAMES = _discover_subcommands()
SCHEDULE_ACTIONS = _discover_schedule_actions()


def test_discovery_found_subcommands():
    assert SUBCOMMAND_NAMES == ["report", "schedule", "collect"]
    assert SCHEDULE_ACTIONS == [
        "report",
        "next",
        "claim",
        "complete",
        "fail",
        "set-status",
        "ready-depth",
        "register",
        "backlog-next",
        "import-ledger",
    ]


@pytest.mark.parametrize("name", SUBCOMMAND_NAMES)
def test_subcommand_help_builds_and_exits_zero(name, capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([name, "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() != ""


@pytest.mark.parametrize("name", SCHEDULE_ACTIONS)
def test_schedule_action_help_builds_and_exits_zero(name, capsys):
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["schedule", name, "--help"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() != ""


@pytest.mark.parametrize(
    "module",
    [
        "seo_manager.analytics.volume_decide",
        "seo_manager.analytics.continuous_optimization_daemon",
        "seo_manager.analytics.metricool_pdf_import",
    ],
)
def test_python_dash_m_help_has_no_runtime_warning(module):
    """``python -m`` must not warn that the module was already imported.

    The analytics package used to import every submodule eagerly, so running
    one of them as ``__main__`` printed runpy's RuntimeWarning.
    """
    proc = subprocess.run(
        [sys.executable, "-W", "error", "-m", module, "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "RuntimeWarning" not in proc.stderr


def test_analytics_public_names_still_resolve_lazily():
    import seo_manager.analytics as analytics

    for name in analytics.__all__:
        assert getattr(analytics, name) is not None, name
    with pytest.raises(AttributeError):
        analytics.not_a_real_export  # noqa: B018
