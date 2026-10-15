#!/usr/bin/env python3
"""
Parse Flask @app.route decorators in app.py and compare to /api/ references in static/dashboard.js.
Writes docs/API_ROUTE_INVENTORY.md (run from repo root: python scripts/generate_route_inventory.py).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"
ROUTES_PKG = ROOT / "routes"
DASHBOARD_JS = ROOT / "static" / "dashboard.js"
TEMPLATE_INDEX = ROOT / "templates" / "index.html"
OUT = ROOT / "docs" / "API_ROUTE_INVENTORY.md"


def _route_decorators_from_function(node: ast.FunctionDef) -> list[tuple[str, frozenset[str]]]:
    """Collect @app.route / @bp.route style rules from a function's decorators."""
    out: list[tuple[str, frozenset[str]]] = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        call = dec
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "route":
            continue
        if not call.args:
            continue
        arg0 = call.args[0]
        if not isinstance(arg0, ast.Constant) or not isinstance(arg0.value, str):
            continue
        rule = arg0.value
        methods: set[str] | None = None
        for kw in call.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                methods = set()
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        methods.add(elt.value.upper())
        if methods is None:
            methods = {"GET"}
        out.append((rule, frozenset(methods)))
    return out


def parse_flask_routes(path: Path) -> list[tuple[str, frozenset[str]]]:
    """Return [(path, methods), ...] from @app.route / @bp.route literals."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    routes: list[tuple[str, frozenset[str]]] = []

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            for rule, methods in _route_decorators_from_function(node):
                routes.append((rule, methods))
            self.generic_visit(node)

    V().visit(tree)
    return routes


def parse_all_project_routes() -> list[tuple[str, frozenset[str]]]:
    """app.py plus routes/*.py blueprints."""
    all_r: list[tuple[str, frozenset[str]]] = parse_flask_routes(APP_PY)
    if ROUTES_PKG.is_dir():
        for py in sorted(ROUTES_PKG.glob("*.py")):
            if py.name.startswith("_") or py.name == "__init__.py":
                continue
            all_r.extend(parse_flask_routes(py))
    return all_r


def parse_dashboard_api_refs(path: Path) -> set[str]:
    """Rough set of path prefixes referenced in dashboard.js."""
    text = path.read_text(encoding="utf-8")
    refs: set[str] = set()
    # fetch('/api/...') or fetch(`/api/...`
    for m in re.finditer(r"['\"](/api/[^'\"?#]+)", text):
        refs.add(m.group(1).rstrip("/"))
    # href="/api/..."
    for m in re.finditer(r'href=["\'](/api/[^"\'?#]+)', text):
        refs.add(m.group(1).rstrip("/"))
    # fetch('/api/foo/' + var → prefix /api/foo
    for m in re.finditer(r"['\"](/api/[^'\"?#]+)/['\"]\s*\+", text):
        refs.add(m.group(1).rstrip("/"))
    return refs


def _rule_static_prefix(rule: str) -> str:
    """Strip Flask variable segments for prefix matching."""
    s = re.sub(r"<[^>]+>", "", rule)
    s = re.sub(r"/+", "/", s)
    return s.rstrip("/") or "/"


def path_matches_ref(rule: str, ref: str) -> bool:
    """True if ref could hit this Flask rule (ignores variable segments)."""
    if rule == ref:
        return True
    pref = _rule_static_prefix(rule)
    if ref == pref or ref.startswith(pref + "/"):
        return True
    parts_rule = rule.strip("/").split("/")
    parts_ref = ref.strip("/").split("/")
    if len(parts_rule) != len(parts_ref):
        i, j = 0, 0
        while i < len(parts_rule) and j < len(parts_ref):
            seg = parts_rule[i]
            if seg.startswith("<") and seg.endswith(">"):
                i += 1
                j += 1
                continue
            if j >= len(parts_ref) or seg != parts_ref[j]:
                return False
            i += 1
            j += 1
        return i == len(parts_rule) and j == len(parts_ref)
    for pr, pf in zip(parts_rule, parts_ref):
        if pr.startswith("<") and pr.endswith(">"):
            continue
        if pr != pf:
            return False
    return True


def classify(rule: str, dashboard_refs: set[str]) -> str:
    for ref in dashboard_refs:
        if path_matches_ref(rule, ref):
            return "ui_dashboard"
    if rule.startswith("/api/telegram") or rule.startswith("/news-sentiment"):
        return "integrations"
    if "<" in rule:
        return "api_dynamic"
    return "api_only"


def main() -> int:
    if not APP_PY.is_file():
        print("app.py not found", file=sys.stderr)
        return 1
    routes = parse_all_project_routes()
    dashboard_refs: set[str] = set()
    if DASHBOARD_JS.is_file():
        dashboard_refs |= parse_dashboard_api_refs(DASHBOARD_JS)
    if TEMPLATE_INDEX.is_file():
        dashboard_refs |= parse_dashboard_api_refs(TEMPLATE_INDEX)

    lines = [
        "# API route inventory",
        "",
        "Auto-generated by `scripts/generate_route_inventory.py`. Regenerate after adding routes.",
        "",
        "| Flask rule | Methods | Classification |",
        "|------------|---------|------------------|",
    ]
    for rule, methods in sorted(routes, key=lambda x: x[0]):
        mstr = ",".join(sorted(methods))
        cls = classify(rule, dashboard_refs)
        lines.append(f"| `{rule}` | {mstr} | {cls} |")

    lines.extend(
        [
            "",
            "## Classification",
            "",
            "- **ui_dashboard**: Path appears in `static/dashboard.js` (fetch or href).",
            "- **api_only**: No matching reference in dashboard (manual, tests, external clients, or future UI).",
            "- **api_dynamic**: Route has `<path:var>` style segments; may still be used from JS with interpolated ids.",
            "- **integrations**: Webhooks or non-dashboard entry points.",
            "",
        ]
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(routes)} routes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
