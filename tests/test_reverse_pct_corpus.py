"""The vendored Legend reverse-PCT corpus is present and well-formed.

This corpus (``vendor/legend-engine/reverse-pct/``) is currently *dormant* -- no
code consumes it yet (a pure-python reverse-PCT adapter needs an executable
Python layer first; see ``TODO.md``). These checks just guard that the pinned
``rev('<pure>', '<python>')`` dataset stays present and non-trivial.
"""

from __future__ import annotations

import pathlib

_CORPUS = pathlib.Path(__file__).resolve().parents[1] / "vendor" / "legend-engine" / "reverse-pct"


def test_corpus_present_for_each_python_target():
    for target in ("python-shared", "python-legendQL", "python-pandasAPI", "framework"):
        assert (_CORPUS / target).is_dir(), f"missing reverse-PCT target dir: {target}"
    pure_files = list(_CORPUS.rglob("*.pure"))
    assert len(pure_files) >= 20


def test_framework_defines_rev_and_corpus_has_pairs():
    framework = (_CORPUS / "framework" / "reversePCT.pure").read_text(encoding="utf-8")
    assert "function meta::pure::test::pct::reversePCT::framework::rev(" in framework

    rev_pairs = sum(text.count("rev(") for text in (p.read_text(encoding="utf-8") for p in _CORPUS.rglob("*.pure")))
    assert rev_pairs > 500  # hundreds of (pure -> python) mappings across the targets
