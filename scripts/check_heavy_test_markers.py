#!/usr/bin/env python3
"""Pre-commit: ensure stress-style tests are marked with pytest.mark.stress.

- tests/stress/conftest.py must set pytestmark including pytest.mark.stress (covers that tree).
- Any tests/**/test_*stress*.py outside tests/stress/ must set the same at module level.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STRESS_CONFTEST = ROOT / "tests" / "stress" / "conftest.py"


def _expr_includes_stress_mark(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Attribute):
        if (
            node.attr == "stress"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "pytest"
        ):
            return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(_expr_includes_stress_mark(elt) for elt in node.elts)
    return False


def _module_sets_stress_pytestmark(tree: ast.Module) -> bool:
    for node in tree.body:
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "pytestmark" and _expr_includes_stress_mark(value):
                return True
    return False


def _check_file(path: Path, label: str) -> list[str]:
    errors: list[str] = []
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except (OSError, SyntaxError) as e:
        return [f"{label}: cannot parse {path}: {e}"]
    if not _module_sets_stress_pytestmark(tree):
        errors.append(
            f"{label}: {path.relative_to(ROOT)} must assign "
            f"pytestmark = pytest.mark.stress (or a list/tuple including it)."
        )
    return errors


def main() -> int:
    errors: list[str] = []
    if not STRESS_CONFTEST.is_file():
        errors.append(f"Missing {STRESS_CONFTEST.relative_to(ROOT)}")
    else:
        errors.extend(_check_file(STRESS_CONFTEST, "Stress tree"))

    stress_dir = (ROOT / "tests" / "stress").resolve()
    for path in sorted((ROOT / "tests").rglob("test_*stress*.py")):
        path = path.resolve()
        try:
            path.relative_to(stress_dir)
        except ValueError:
            errors.extend(_check_file(path, "Heavy test file"))

    if errors:
        print("pytest.mark.stress marker check failed:\n", file=sys.stderr)
        for msg in errors:
            print(f"  - {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
