"""Export the OpenAPI document to docs/openapi.json.

Usage: cd services && uv run python api/export_openapi.py"""

from __future__ import annotations

import json
import pathlib
import sys

from orbit_api.main import create_app


def main(out: str | None = None) -> pathlib.Path:
    target = (
        pathlib.Path(out) if out else pathlib.Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n")
    return target


if __name__ == "__main__":
    print(main(sys.argv[1] if len(sys.argv) > 1 else None))
