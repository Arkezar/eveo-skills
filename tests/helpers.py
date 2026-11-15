"""Load skill scripts by path (each lives in its own skills/<name>/scripts/ dir) and fixtures."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_script(skill: str, script: str):
    path = ROOT / "skills" / skill / "scripts" / f"{script}.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(script, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
