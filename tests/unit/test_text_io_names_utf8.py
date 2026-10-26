"""Every text-mode file open in the package names its encoding.

Without ``encoding=`` Python uses the locale encoding: UTF-8 on Linux, but
cp1252 on Windows, where reading a UTF-8 state or credentials file that holds a
non-ASCII character raises ``UnicodeDecodeError`` (the Windows CI leg found
this in the test suite first). The check parses the source, so it cannot pass
by accident on a UTF-8 machine.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "seo_manager"
TEXT_METHODS = {"read_text", "write_text"}


def _mode(call):
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        return call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return "r"


def _unnamed_encoding_calls(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            if "b" not in str(_mode(node)):
                yield node.lineno
        elif isinstance(func, ast.Attribute) and func.attr in TEXT_METHODS:
            yield node.lineno


def test_text_io_names_its_encoding():
    offenders = [
        f"{path.relative_to(PACKAGE.parent)}:{line}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for line in _unnamed_encoding_calls(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
    ]
    assert not offenders, "text I/O without encoding=: " + ", ".join(offenders)
