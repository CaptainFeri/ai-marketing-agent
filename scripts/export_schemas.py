#!/usr/bin/env python
"""Write the agent JSON Schemas to ``schemas/``.

Run after changing anything in ``app/agents/contracts.py``:

    .venv/bin/python scripts/export_schemas.py

The files are committed so the prompts, the panel and any external tooling can
read the contract without importing the application. CI fails if they are
stale (``tests/test_agent_contracts.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.registry import export_schemas  # noqa: E402


def main() -> int:
    written = export_schemas()
    for path in written:
        print(path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path)
    print(f"{len(written)} schemas written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
