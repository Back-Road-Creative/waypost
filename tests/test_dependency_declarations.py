"""Guards against packaging-declaration drift.

The package is installed from pyproject.toml (`pip install ".[dev]"`), never
from requirements.txt, so anything declared only in requirements.txt is absent
from every `pip install .` environment.

That is how google-auth-oauthlib went missing: youtube_sync.py imported it,
requirements.txt declared it, pyproject.toml did not, and a try/except
ImportError turned the resulting absence into a printed warning nobody reads.

These checks read the declaration files as text and never import the
distributions, so they cannot pass by accident on a machine that happens to
have a package installed.
"""

import re
from pathlib import Path

import tomllib

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


def _requirements_txt():
    lines = (PROJECT_ROOT / "requirements.txt").read_text().splitlines()
    return {
        _requirement_name(line)
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_pyproject_declares_everything_requirements_txt_does():
    """requirements.txt is never installed here, so pyproject must cover it.

    Without this, a dependency can be "declared" in a file no installer reads
    and still be missing everywhere it matters.
    """
    missing = sorted(_requirements_txt() - _declared())
    assert not missing, (
        "declared in requirements.txt but not in pyproject.toml, so no CI run "
        f"and no `pip install .` ever installs them: {missing}"
    )


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
