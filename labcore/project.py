"""Project-relative resources and portable runtime metadata (no credentials)."""
import importlib.metadata
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]


def runtime_info():
    dependencies = {}
    for name in ("numpy", "scipy", "PyYAML", "lmfit", "odrpack", "punpy"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": sys.platform, "dependencies": dependencies}
