"""Subprocess client for the ``legend-bridge`` JVM harness.

Each call launches ``java -jar legend-bridge.jar <command>``, writes the payload
to the process's stdin and reads the result from stdout. The harness is built
separately (``mvn -f legend-bridge package``); if the jar (or ``java``) is not
present, :meth:`LegendBridge.available` returns ``False`` so tests can skip.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_JAR = _REPO_ROOT / "legend-bridge" / "target" / "legend-bridge.jar"


class LegendBridgeError(RuntimeError):
    """Raised when the Legend harness exits non-zero (e.g. a Pure parse error)."""


def _find_java() -> str | None:
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / "java"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("java")


class LegendBridge:
    """Bridge to the real Legend engine via the ``legend-bridge`` jar."""

    def __init__(self, jar: str | os.PathLike[str] | None = None, java: str | None = None) -> None:
        env_jar = os.environ.get("PURE_PYTHON_LEGEND_BRIDGE_JAR")
        self.jar = Path(jar or env_jar or _DEFAULT_JAR)
        self.java = java or _find_java()

    def available(self) -> bool:
        return self.java is not None and self.jar.is_file()

    def _run(self, command: str, payload: str) -> str:
        if not self.available():
            raise LegendBridgeError(
                f"legend-bridge not available (java={self.java}, jar={self.jar}); "
                "build it with `mvn -f legend-bridge package`"
            )
        proc = subprocess.run(
            [self.java, "-jar", str(self.jar), command],
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            raise LegendBridgeError(
                f"legend-bridge `{command}` failed (exit {proc.returncode}): "
                + proc.stderr.decode("utf-8", "replace").strip()
            )
        return proc.stdout.decode("utf-8")

    def parse(self, pure_text: str) -> dict[str, Any]:
        """Parse Pure grammar text into Legend's ``PureModelContextData`` (as a dict)."""
        return json.loads(self._run("parse", pure_text))

    def compose(self, model: dict[str, Any] | str) -> str:
        """Render a ``PureModelContextData`` (dict or JSON string) back to Pure grammar."""
        payload = model if isinstance(model, str) else json.dumps(model)
        return self._run("compose", payload)
