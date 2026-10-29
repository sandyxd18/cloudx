#!/usr/bin/env python3
"""Check that the endpoints shown in docs/feature-comparison.md exist as routes in services/*/app.

Two kinds of cell are read:
  * any code span that starts with an HTTP method (`POST /servers/{ref}/start`, `GET\\|DELETE /flavors`,
    `POST /servers/{ref}/confirmResize\\|revertResize`), wherever it appears;
  * any code span that starts with `/` inside the "Endpoint" column of a table (`/v3/domains`), where any method counts.

Parameter names are not compared (`{ref}` matches `{rule_id}`), and neither are query strings. The check runs one way: it
does not report routes that the page leaves out.

    python scripts/check_feature_comparison.py
    python scripts/check_feature_comparison.py path/to/other.md
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "feature-comparison.md"

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
ROUTE = re.compile(r"""@\w+\.(get|post|put|patch|delete)\(\s*f?(["'])(.*?)\2""", re.S)
SPAN = re.compile(r"`([^`]+)`")


def norm(path: str) -> str:
    path = re.sub(r"\{[^}]*\}", "{}", path.split("?")[0])
    return path.rstrip("/") or "/"


def collect_routes(root: Path = ROOT) -> set[tuple[str, str]]:
    routes = set()
    for source in (root / "services").glob("*/app/**/*.py"):
        for method, _, path in ROUTE.findall(source.read_text()):
            routes.add((method.upper(), norm(path)))
    return routes


def candidates(span: str):
    """Yield (methods, path) for one code span, or nothing when the span is not an endpoint."""
    head, _, rest = span.replace("\\|", "|").partition(" ")
    methods = head.split("|")
    if all(m in METHODS for m in methods) and rest.startswith("/"):
        path = rest.split()[0]
    elif head.startswith("/"):
        methods, path = [None], head
    else:
        return
    # `/x/confirmResize|revertResize`: the last segment has alternatives
    prefix, _, last = path.rpartition("/")
    for alt in last.split("|"):
        yield methods, f"{prefix}/{alt}"


def endpoint_column(lines: list[str]):
    """Yield (line number, cell text) for the cells under a table header named "Endpoint"."""
    column = None
    for number, line in enumerate(lines, 1):
        if not line.startswith("|"):
            column = None
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if column is None:
            column = next((i for i, c in enumerate(cells) if c.lower() == "endpoint"), -1)
        elif column >= 0 and column < len(cells) and not set(cells[column]) <= set("-: "):
            yield number, cells[column]


def endpoints(doc: Path = DOC):
    """Yield (line number, span, methods, path) for every endpoint the page names. `methods` is [None] for a bare path."""
    lines = doc.read_text().splitlines()
    cells = {}
    for number, cell in endpoint_column(lines):
        cells.setdefault(number, []).append(cell)
    for number, line in enumerate(lines, 1):
        for span in SPAN.findall(line):
            for methods, path in candidates(span):
                # a bare path outside an Endpoint column is prose, not an endpoint
                if methods != [None] or any(span in cell for cell in cells.get(number, [])):
                    yield number, span, methods, path


def problems(doc: Path = DOC, routes: set[tuple[str, str]] | None = None) -> list[str]:
    routes = collect_routes() if routes is None else routes
    paths = {p for _, p in routes}
    found, seen = [], set()
    for number, span, methods, path in endpoints(doc):
        path = norm(path)
        ok = path in paths if methods == [None] else any((m, path) in routes for m in methods)
        if not ok and (number, span) not in seen:
            seen.add((number, span))
            hint = " (the path exists with another method)" if path in paths else ""
            found.append(f"{doc.name}:{number}: `{span}` has no route{hint}")
    return found


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DOC
    bad = problems(target)
    print("\n".join(bad) if bad else f"ok: every endpoint in {target.name} is a route")
    sys.exit(1 if bad else 0)
