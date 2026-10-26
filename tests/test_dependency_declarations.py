"""Guards against packaging-declaration drift.

The package is installed from pyproject.toml (`pip install ".[dev]"`), so a
dependency declared anywhere else is absent from every `pip install .`
environment.

That is how google-auth-oauthlib went missing: youtube_sync.py imported it,
a requirements file declared it, pyproject.toml did not, and a try/except
ImportError turned the resulting absence into a printed warning nobody reads.

These checks read the declaration files as text and never import the
distributions, so they cannot pass by accident on a machine that happens to
have a package installed.
"""

import configparser
import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _normalize(name):
    """PEP 503 normalized distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(spec):
    """Strip version specifiers, extras and markers from a requirement line."""
    return _normalize(re.split(r"[<>=!~;\[\s]", spec.strip(), maxsplit=1)[0])


def _declared():
    """Every distribution pyproject.toml installs, runtime plus extras."""
    cfg = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    specs = list(cfg["project"]["dependencies"])
    for extra in cfg["project"].get("optional-dependencies", {}).values():
        specs.extend(extra)
    return {_requirement_name(spec) for spec in specs}


def test_pyproject_is_the_only_dependency_declaration():
    """A second requirements file is installed by nothing and drifts.

    pyproject.toml (plus the requirements.lock constraints) is what every
    install path reads, including pip installs of this repo by git sha, so no
    top-level requirements.txt may exist to disagree with it.
    """
    assert not (PROJECT_ROOT / "requirements.txt").exists(), (
        "requirements.txt duplicates pyproject.toml and is read by no installer"
    )


def test_pytest_is_configured_in_pytest_ini_only():
    """pytest.ini shadows [tool.pytest.ini_options], so that table is dead."""
    cfg = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    assert "pytest" not in cfg.get("tool", {}), (
        "[tool.pytest.ini_options] in pyproject.toml is shadowed by pytest.ini"
    )


def test_pytest_ini_declares_no_unused_markers():
    """Declared markers that no test uses only hide misspelled ones."""
    parser = configparser.ConfigParser()
    parser.read(PROJECT_ROOT / "pytest.ini")
    markers = parser.get("pytest", "markers", fallback="")
    used = set()
    for path in (PROJECT_ROOT / "tests").rglob("*.py"):
        used.update(re.findall(r"pytest\.mark\.(\w+)", path.read_text()))
    declared = set(re.findall(r"^\s*(\w+):", markers, re.M))
    builtin = {
        "parametrize",
        "xfail",
        "skip",
        "skipif",
        "usefixtures",
        "filterwarnings",
    }
    unused = sorted(declared - used - builtin)
    assert not unused, f"markers declared in pytest.ini but used by no test: {unused}"


def test_google_api_distributions_are_declared():
    """Production code imports these three roots unconditionally.

    `googleapiclient` ships in google-api-python-client, `google.oauth2` and
    `google.auth.transport.requests` in google-auth, and
    `google_auth_oauthlib.flow` in google-auth-oauthlib. Only the first is a
    dependency of another declared package, so the other two have to be named
    here or they arrive by luck -- or, as with google-auth-oauthlib, not at all.
    """
    declared = _declared()
    for distribution in (
        "google-api-python-client",
        "google-auth",
        "google-auth-oauthlib",
    ):
        assert distribution in declared, f"{distribution} is not declared"


def test_python_floor_is_3_12_everywhere_pyproject_states_it():
    """CI tests Python 3.12 only, so no lower version may be claimed.

    `>=3.8` was false (the code uses `tuple[...]` annotations and zoneinfo)
    and CI never ran anything but 3.12. The floor, the classifiers and the
    two tool targets are one decision and must move together.
    """
    cfg = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    assert cfg["project"]["requires-python"] == ">=3.12"
    versions = [
        c
        for c in cfg["project"]["classifiers"]
        if c.startswith("Programming Language :: Python")
    ]
    assert versions == [
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
    ]
    assert cfg["tool"]["mypy"]["python_version"] == "3.12"
    assert cfg["tool"]["ruff"]["target-version"] == "py312"


def test_tzdata_is_declared_for_windows():
    """Windows ships no IANA time-zone database, so ``zoneinfo.ZoneInfo`` raises
    ``ZoneInfoNotFoundError`` there unless the ``tzdata`` package is installed.
    The scheduler builds its zones at import time, so without it the package
    cannot even be imported on Windows.
    """
    cfg = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    specs = [s.replace(" ", "") for s in cfg["project"]["dependencies"]]
    assert any(
        s.startswith("tzdata") and 'sys_platform=="win32"' in s for s in specs
    ), specs
