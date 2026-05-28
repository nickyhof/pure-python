"""Vendored ANTLR parsers generated from legend-pure's own grammars.

Two grammar sets, both generated (ANTLR 4.13.2, Python3 target) from the ``.g4``
sources alongside them, copied verbatim from ``legend-pure`` (the
``serialization/grammar`` trees of ``legend-pure-m3-core`` / ``legend-pure-m4``;
the generated parser/lexer classes ship in those 5.86.0 jars):

- ``M3CoreParser`` / ``M3CoreLexer`` -- the *readable* class grammar (``Class``,
  ``Association``, ``Enum``, ``Profile``), walked by
  :mod:`pure_python.codegen.grammar`. The only edit is the
  ``options { tokenVocab=M3CoreLexer; }`` line added to ``M3CoreParser.g4`` so
  the split lexer/parser pair can be generated standalone.
- ``M4AntlrParser`` / ``M4AntlrLexer`` -- the low-level **M4** instance syntax
  the bootstrap ``m3.pure`` is written in, walked by
  :mod:`pure_python.codegen.parser`.

Both lexers ``import M4Fragment`` (the shared fragment grammar, one copy here).

To regenerate after bumping the grammar sources::

    java -cp <antlr4-4.13.2+ deps> org.antlr.v4.Tool -Dlanguage=Python3 \
        M3CoreLexer.g4 M3CoreParser.g4 M4AntlrLexer.g4 M4AntlrParser.g4

Requires the ``antlr4-python3-runtime`` package (a hard dependency).
"""
