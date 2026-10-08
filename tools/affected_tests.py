"""Print the pytest marker expression for the test categories a change can affect.

    uv run pytest -m "$(uv run python tools/affected_tests.py)"      # vs. HEAD (working tree)
    uv run python tools/affected_tests.py origin/master              # vs. another ref

Categories (see tests/conftest.py): technical, validation, behaviour. Unknown paths or
changes to core modules (model, geometry, params) select everything.
"""

from __future__ import annotations

import subprocess
import sys

ALL = {"technical", "validation", "behaviour"}
# (path prefix, categories); the first match wins
RULES: list[tuple[str, set[str]]] = [
    ("tests/test_", set()),  # a test file: run it directly
    ("docs/", set()),
    ("tools/", set()),
    ("README", set()),
    ("src/roomplanner/export/", {"technical"}),
    ("src/roomplanner/render/", {"technical"}),
    ("src/roomplanner/ui/", {"technical"}),
    ("src/roomplanner/cli.py", {"technical"}),
    ("src/roomplanner/i18n.py", {"technical"}),
    ("src/roomplanner/serialization.py", {"technical"}),
    ("src/roomplanner/bitgrid.py", {"technical", "behaviour", "validation"}),
    ("src/roomplanner/validation.py", {"validation"}),
    ("src/roomplanner/buildingcode.py", {"validation"}),
    ("src/roomplanner/rules.py", {"validation", "behaviour"}),
    ("src/roomplanner/pipeline/", {"behaviour", "validation"}),
    ("src/roomplanner/data/", {"behaviour", "validation", "technical"}),
]


def categories(paths: list[str]) -> set[str]:
    found: set[str] = set()
    for path in paths:
        for prefix, cats in RULES:
            if path.startswith(prefix):
                found |= cats
                break
        else:
            return ALL
    return found


def main() -> None:
    ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    out = subprocess.run(
        ["git", "diff", "--name-only", ref], check=True, capture_output=True, text=True
    ).stdout.split()
    cats = categories(out)
    print(" or ".join(sorted(cats)) if cats else "technical")


if __name__ == "__main__":
    main()
