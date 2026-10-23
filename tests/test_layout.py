"""Layout guard: a test module name belongs to exactly one place under tests/.

Two modules sharing a basename (for example tests/test_x.py and
tests/unit/test_x.py) are easy to confuse and hide which file a failure
belongs to. conftest.py and __init__.py legitimately repeat per package,
so they are skipped.
"""

from collections import defaultdict
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
ALLOWED_REPEATS = {"conftest.py", "__init__.py"}


def _basename_locations() -> dict[str, list[str]]:
    seen: dict[str, list[str]] = defaultdict(list)
    for path in TESTS_ROOT.rglob("*.py"):
        if path.name in ALLOWED_REPEATS or "__pycache__" in path.parts:
            continue
        seen[path.name].append(path.relative_to(TESTS_ROOT).as_posix())
    return seen


def test_no_test_module_basename_is_repeated():
    duplicates = {
        name: sorted(paths)
        for name, paths in _basename_locations().items()
        if len(paths) > 1
    }
    assert not duplicates, f"Test modules share a basename: {duplicates}"
