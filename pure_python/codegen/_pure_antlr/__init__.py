"""Vendored ANTLR parsers generated from legend-pure's own grammars.

The ``.g4`` grammar sources live under ``vendor/legend-pure/grammar/`` (verbatim
FINOS sources, pinned in ``vendor/legend-pure/SOURCE.txt``). The ``*.py`` here
are the generated output (ANTLR 4.13.2, Python3 target) and are walked directly
by :mod:`pure_python.codegen.parser` (M4) and :mod:`pure_python.codegen.grammar`
(M3Core) -- the generated listener/visitor are not produced or used.

Two grammar sets:

- ``M3CoreParser`` / ``M3CoreLexer`` -- the *readable* class grammar (``Class``,
  ``Association``, ``Enum``, ``Profile``).
- ``M4AntlrParser`` / ``M4AntlrLexer`` -- the low-level **M4** instance syntax
  the bootstrap ``m3.pure`` is written in.

Both lexers ``import M4Fragment`` (shared fragments); ANTLR inlines it at
generation time, so there is no ``M4Fragment.py`` and it is not needed at
runtime -- only to regenerate.

To regenerate after refreshing the grammar sources, from ``vendor/legend-pure/grammar``::

    java -cp <antlr4-4.13.2+ deps> org.antlr.v4.Tool -Dlanguage=Python3 \
        -no-listener -no-visitor -o <this directory> \
        M3CoreLexer.g4 M3CoreParser.g4 M4AntlrLexer.g4 M4AntlrParser.g4

Requires the ``antlr4-python3-runtime`` package (a hard dependency).
"""
