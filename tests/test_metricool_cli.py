"""The Metricool PDF importer's usage line shows which flags are required."""

import re
import sys

import pytest

from seo_manager.analytics.metricool_pdf_import import PDFTextExtractor, main


def _usage(monkeypatch, capsys) -> str:
    monkeypatch.setattr(sys, "argv", ["metricool_pdf_import.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    return out.split("\n\n", 1)[0]


def test_usage_shows_required_flags_without_brackets(monkeypatch, capsys):
    usage = " ".join(_usage(monkeypatch, capsys).split())
    assert not re.search(r"\[--site-id[^\]]*\]", usage)
    assert not re.search(r"\[--accounts[^\]]*\]", usage)
    assert "--site-id SITE_ID" in usage
    assert "--accounts ACCOUNTS" in usage


def test_usage_shows_dump_text_as_an_alternative(monkeypatch, capsys):
    usage = " ".join(_usage(monkeypatch, capsys).split())
    assert re.search(r"--dump-text\s*\|", usage) or re.search(r"\|\s*--dump-text", usage)


def test_dump_text_needs_no_site_or_accounts(monkeypatch, capsys, tmp_path):
    txt = tmp_path / "report.pdf"
    txt.write_text("pdf")
    monkeypatch.setattr(
        PDFTextExtractor, "extract", lambda self, path, layout=True: "hello metricool"
    )
    monkeypatch.setattr(sys, "argv", ["x", str(txt), "--dump-text"])
    main()
    assert "hello metricool" in capsys.readouterr().out


def test_import_without_site_id_is_an_error(monkeypatch, capsys, tmp_path):
    txt = tmp_path / "report.extracted.txt"
    txt.write_text("x")
    monkeypatch.setattr(sys, "argv", ["x", str(txt)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "--site-id is required" in capsys.readouterr().err
