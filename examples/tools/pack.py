#!/usr/bin/env python3
"""Build every example into OUT_DIR/<id>.sb3 plus OUT_DIR/index.json.

    python3 examples/tools/pack.py OUT_DIR

Deterministic: running it twice produces byte-identical files.
"""
import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFS = HERE.parent / "defs"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(DEFS))
sys.dont_write_bytecode = True


def load(path):
    spec = importlib.util.spec_from_file_location("example_" + path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    out = pathlib.Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for path in sorted(DEFS.glob("[0-9][0-9]_*.py")):
        mod = load(path)
        meta = mod.META
        target = out / (meta["id"] + ".sb3")
        mod.build().save_sb3(target)
        index.append({k: meta[k] for k in ("id", "title", "description", "level", "needs", "world")})
        print("%-24s %6d bytes" % (target.name, target.stat().st_size))
    text = json.dumps(index, indent=2, ensure_ascii=True) + "\n"
    (out / "index.json").write_bytes(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
