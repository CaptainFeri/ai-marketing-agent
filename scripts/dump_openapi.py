"""Dump the FastAPI app's own OpenAPI document.

Used by ``panel/package.json``'s ``gen:api`` script as the source of truth
for the panel's generated TypeScript client — see ``panel/docs/api-client.md``.
Importing ``app.main`` is enough; no server needs to be running.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <output-path>")

    from app.main import app

    output = Path(sys.argv[1])
    output.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
