"""Bridge to the real Legend (FINOS) engine running on the JVM.

``pure_python`` is a structural representation of Pure; it does not evaluate
Pure. This package shells out to a small Java harness (``legend-bridge``) built
on the published ``legend-engine`` artifacts to (a) validate the Pure that
pure-python emits against Legend's *real* grammar and (b) **execute** Pure and
return results -- delegating execution to Legend rather than reimplementing it.

The harness is optional: build it with ``mvn -f legend-bridge package`` and the
:class:`~pure_python.legend.bridge.LegendBridge` will find the shaded jar (or
point ``PURE_PYTHON_LEGEND_BRIDGE_JAR`` at it).
"""

from __future__ import annotations

from .bridge import LegendBridge, LegendBridgeError

__all__ = ["LegendBridge", "LegendBridgeError"]
