"""Generate a PCT (Pure Compatibility Test) compatibility report.

Companion to Legend's own ``GeneratePCT``: it renders a Markdown matrix of the
Pure function library with a single **pure-python** column showing how many of
each function's PCT tests pure-python can pass.

It reads the PCT data Legend ships as JSON, vendored under
``vendor/legend-pure/pct/`` (one pair per *group* -- essential, grammar,
standard, relation, unclassified, variant):

* ``FUNCTIONS_<group>.json``               -- the function definitions (the rows).
* ``ADAPTER_<group>_compiled_Native.json`` -- Legend's compiled-execution results,
  used here only to *enumerate* each function's PCT tests (the denominator). The
  pass/fail those files record is Legend's own, kept as the target baseline; it
  is **not** pure-python's result.

The pure-python column reflects what **pure-python itself** can do. Today that is
nothing: pure-python is a structural metamodel + grammar round-trip with no
expression/evaluation layer yet (see ``TODO.md`` Tier 2), so it cannot execute --
or even represent -- a PCT test function. Every test is therefore a fail. This is
the honest baseline; the column will light up as pure-python gains the ability to
represent and run Pure. No JVM is needed to render the report (offline, pinned
snapshot -- see ``vendor/legend-pure/SOURCE.txt``).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

_PCT_DIR = Path(__file__).resolve().parents[2] / "vendor" / "legend-pure" / "pct"
_GROUPS = ("essential", "grammar", "standard", "relation", "unclassified", "variant")


def _pure_python_passes(test: dict, function_source_id: str) -> bool:
    """Whether pure-python can pass this PCT test.

    Currently always ``False``: pure-python has no expression/evaluation layer,
    so it cannot run any PCT test. This is the single extension point -- once
    pure-python can represent and execute (or faithfully round-trip) a test,
    return ``True`` for the ones it handles and the report column will reflect it.
    """
    return False


@dataclass
class FunctionRow:
    group: str
    name: str
    source_id: str
    declared_tests: int  # pctTestCount from the function definition (0 if unknown)
    passed: int = 0
    total: int = 0

    @property
    def cell(self) -> str:
        if self.total == 0:
            return "&empty;"  # no PCT tests for this function
        return f"{self.passed}/{self.total}"


@dataclass
class Report:
    rows: list[FunctionRow]

    def by_group(self) -> dict[str, list[FunctionRow]]:
        out: dict[str, list[FunctionRow]] = {g: [] for g in _GROUPS}
        for row in self.rows:
            out.setdefault(row.group, []).append(row)
        return {g: rows for g, rows in out.items() if rows}

    def totals(self) -> tuple[int, int]:
        return sum(r.passed for r in self.rows), sum(r.total for r in self.rows)


def _name_from_source(source_id: str) -> str:
    leaf = source_id.rsplit("/", 1)[-1]
    return leaf[:-5] if leaf.endswith(".pure") else leaf


def load_report(pct_dir: Path = _PCT_DIR) -> Report:
    """Rows come from FUNCTIONS_*; the PCT test count per row from ADAPTER_*."""
    rows: dict[str, FunctionRow] = {}

    for group in _GROUPS:
        functions_file = pct_dir / f"FUNCTIONS_{group}.json"
        if not functions_file.is_file():
            continue
        data = json.loads(functions_file.read_text(encoding="utf-8"))
        for fn in data.get("functionDefinitions", []):
            source_id = fn["sourceId"]
            rows[source_id] = FunctionRow(
                group=group,
                name=fn.get("name") or _name_from_source(source_id),
                source_id=source_id,
                declared_tests=fn.get("pctTestCount") or fn.get("testCount") or 0,
            )

    for group in _GROUPS:
        adapter_file = pct_dir / f"ADAPTER_{group}_compiled_Native.json"
        if not adapter_file.is_file():
            continue
        data = json.loads(adapter_file.read_text(encoding="utf-8"))
        for function_test in data.get("functionTests", []):
            source_id = function_test["sourceId"]
            row = rows.get(source_id)
            if row is None:  # tests for a source with no declared function (composition tests)
                row = FunctionRow(group=group, name=_name_from_source(source_id), source_id=source_id, declared_tests=0)
                rows[source_id] = row
            for test in function_test.get("tests", []):
                row.total += 1
                if _pure_python_passes(test, source_id):
                    row.passed += 1

    ordered = sorted(rows.values(), key=lambda r: (_GROUPS.index(r.group) if r.group in _GROUPS else 99, r.source_id))
    return Report(ordered)


def render_markdown(report: Report) -> str:
    grand_passed, grand_total = report.totals()
    pct = f" ({100 * grand_passed // grand_total}%)" if grand_total else ""
    lines: list[str] = [
        "# PCT compatibility report",
        "",
        "Pure Compatibility Test results for the **pure-python** target. The "
        "pure-python column counts how many of each function's PCT tests pure-python "
        "can pass. It currently passes **none**: pure-python is a structural "
        "metamodel with no expression/evaluation layer yet (see `TODO.md`), so it "
        "cannot run a PCT test. This is the honest baseline -- the column will "
        "improve as pure-python gains the ability to represent and execute Pure. "
        "Generated by `python -m pure_python.report.pct` from the pinned PCT data in "
        "`vendor/legend-pure/pct/`. `&empty;` = no PCT tests.",
        "",
        f"**Overall: {grand_passed}/{grand_total} tests passing{pct}.**",
        "",
        "| Group | Passed | Total |",
        "| --- | --- | --- |",
    ]
    grouped = report.by_group()
    for group, group_rows in grouped.items():
        gp = sum(r.passed for r in group_rows)
        gt = sum(r.total for r in group_rows)
        lines.append(f"| {group} | {gp} | {gt} |")
    lines.append("")

    for group, group_rows in grouped.items():
        lines += [f"## {group}", "", "| Function | pure-python |", "| --- | --- |"]
        for row in group_rows:
            lines.append(f"| `{row.name}` | {row.cell} |")
        lines.append("")

    return "\n".join(lines)


def generate(out_path: Path, pct_dir: Path = _PCT_DIR) -> Report:
    report = load_report(pct_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def _main() -> None:
    parser = argparse.ArgumentParser(description="Generate the pure-python PCT compatibility report.")
    parser.add_argument("--out", type=Path, default=Path("docs/PCT_Report.md"), help="output Markdown path")
    parser.add_argument("--pct-dir", type=Path, default=_PCT_DIR, help="directory of vendored PCT JSON")
    args = parser.parse_args()

    report = generate(args.out, args.pct_dir)
    passed, total = report.totals()
    pct = f" ({100 * passed // total}%)" if total else ""
    print(f"PCT report: {passed}/{total} tests passing{pct} across {len(report.rows)} functions -> {args.out}")
    for group, rows in report.by_group().items():
        gp = sum(r.passed for r in rows)
        gt = sum(r.total for r in rows)
        print(f"  {group:13} {gp}/{gt}")


if __name__ == "__main__":
    _main()
