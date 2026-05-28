"""Vendored ANTLR parser generated from legend-pure's own Pure grammar.

``M3CoreParser.py`` / ``M3CoreLexer.py`` are generated (ANTLR 4.13.2, Python3
target) from the ``.g4`` sources alongside them, which are copied verbatim from
``legend-pure`` (``legend-pure-m3-core`` / ``legend-pure-m4`` 5.86.0; the only
edit is the ``options { tokenVocab=M3CoreLexer; }`` line added to
``M3CoreParser.g4`` so the split lexer/parser pair can be generated standalone).

To regenerate after bumping the grammar sources::

    java -cp <antlr4-4.13.2+ deps> org.antlr.v4.Tool -Dlanguage=Python3 M3CoreLexer.g4
    java -cp <antlr4-4.13.2+ deps> org.antlr.v4.Tool -Dlanguage=Python3 M3CoreParser.g4

Requires the ``antlr4-python3-runtime`` package (a hard dependency).
:mod:`pure_python.codegen.grammar` walks the parse tree.
"""
