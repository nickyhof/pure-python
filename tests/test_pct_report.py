"""The PCT compatibility report loads the vendored data and renders Markdown.

Fully offline: it reads the pinned ``vendor/legend-pure/pct/`` snapshot, so no
JVM/bridge is required.
"""

from __future__ import annotations

from pure_python.report import pct


def test_load_report_joins_functions_and_results():
    report = pct.load_report()
    assert len(report.rows) > 200  # the vendored corpus has hundreds of functions
    passed, total = report.totals()
    assert 0 < passed <= total
    # Every group with data is one of the known PCT groups.
    assert set(report.by_group()) <= set(pct._GROUPS)
    # A function that has compiled-adapter results carries a passed/total cell.
    tested = [r for r in report.rows if r.total > 0]
    assert tested and all(r.passed <= r.total for r in tested)


def test_render_markdown_has_pure_python_column_and_summary():
    report = pct.load_report()
    md = pct.render_markdown(report)
    assert "# PCT compatibility report" in md
    assert "| Function | pure-python |" in md  # the single pure-python column
    assert "Overall:" in md and "tests passing" in md
    # Known essential functions appear, with a passed/total cell.
    assert "`at`" in md
    passed, total = report.totals()
    assert f"{passed}/{total}" in md or f"Overall: {passed}/{total}" in md


def test_generate_writes_file(tmp_path):
    out = tmp_path / "PCT_Report.md"
    report = pct.generate(out)
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert text == pct.render_markdown(report)
    assert "pure-python" in text
