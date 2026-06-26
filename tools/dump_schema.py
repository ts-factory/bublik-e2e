#!/usr/bin/env python3
"""Emit the e2e manifest JSON Schema from the Pydantic models.

The ``Manifest`` model in :mod:`core.manifest_models` is the single source of
truth for the manifest shape. This script renders it to JSON Schema and writes
``schema/e2e-manifest.schema.json`` (committed). The UI repo consumes that file
to generate its Zod validators, so regenerate and commit it whenever the models
change. A drift test (``tests/test_manifest_schema.py``) fails CI otherwise.

Usage::

    python tools/dump_schema.py            # write schema/e2e-manifest.schema.json
    python tools/dump_schema.py --check    # exit 1 if the committed file is stale
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.manifest_models import manifest_json_schema  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "schema" / "e2e-manifest.schema.json"


def render() -> str:
    return json.dumps(manifest_json_schema(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed schema is up to date instead of writing it",
    )
    args = parser.parse_args()

    rendered = render()
    if args.check:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.is_file() else ""
        if current != rendered:
            print(
                f"{SCHEMA_PATH} is stale; run `python tools/dump_schema.py`",
                file=sys.stderr,
            )
            return 1
        print(f"{SCHEMA_PATH} is up to date")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(rendered, encoding="utf-8")
    print(str(SCHEMA_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
