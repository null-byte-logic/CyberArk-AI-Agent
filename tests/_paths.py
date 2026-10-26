import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agent"):          # agent/ so Lambda code can `import safety`
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
