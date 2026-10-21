"""Services must not import from the routes.

The weekly report, the alerts and the narrative once imported their SQL straight from
app/routes/metrics.py, so a route module was the only place the figures lived. They now come from
app/services/metrics. This fails if a service reaches back into a route.
"""
import ast
from pathlib import Path

SERVICES = Path(__file__).resolve().parent.parent / "app" / "services"


def _imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            if node.module == "app":
                for alias in node.names:
                    yield f"app.{alias.name}"


def test_the_walk_finds_the_services():
    # If the folder moved, the check below would pass by looking at nothing.
    assert len(list(SERVICES.rglob("*.py"))) > 15


def test_no_service_imports_from_the_routes():
    offenders = sorted(
        f"{path.relative_to(SERVICES.parent)}: {module}"
        for path in SERVICES.rglob("*.py")
        for module in _imported_modules(path)
        if module == "app.routes" or module.startswith("app.routes.")
    )
    assert not offenders, f"Services that import from routes: {offenders}"
