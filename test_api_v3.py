#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "rich>=13",
# ]
# ///
"""Smoke test for the Wagtail REST API v3 (django-ninja) exposed by the test site.

Run the test site first:

    export DJANGO_SETTINGS_MODULE=wagtail.test.settings_ui
    uv run ./wagtail/test/manage.py migrate
    uv run ./wagtail/test/manage.py loaddata wagtail/test/demosite/fixtures/demosite.json
    uv run ./wagtail/test/manage.py shell -c "
    from django.contrib.auth import get_user_model
    from wagtail.api.v3.models import ApiToken
    U = get_user_model()
    u, _ = U.objects.update_or_create(username='admin', defaults={
        'email': 'admin@example.com', 'is_staff': True, 'is_superuser': True,
    })
    u.set_password('changeme'); u.save()
    token, _ = ApiToken.objects.update_or_create(
        user=u, label='smoke', defaults={'revoked': False},
    )
    print('BEARER_TOKEN=' + token.key)
    "
    uv run ./wagtail/test/manage.py runserver 0:8001

Then either:

    ./test_api_v3.py                                    # public-only checks
    ./test_api_v3.py --admin-token "$BEARER_TOKEN"      # adds write demo
    ./test_api_v3.py --admin-user admin                 # session-auth alternative
    ./test_api_v3.py --show-body                        # dump JSON per response
    ./test_api_v3.py --filter publish                   # run a subset by name substring
    ./test_api_v3.py --schema                           # just print the OpenAPI summary

Two key differences vs. the v2 script:

* v3 prefers bearer-token auth (no CSRF dance, no cookie jar). Pass
  ``--admin-token`` and the script attaches ``Authorization: Bearer <key>`` to
  every write. Session auth is still supported via ``--admin-user`` for
  completeness — that path follows the same CSRF flow as the v2 admin API.
* v3 publishes an OpenAPI document at ``/api/v3/openapi.json``. The
  ``--schema`` flag (or the default end-of-run summary) prints what it
  contains so you can confirm new endpoints land in the spec.

The destructive admin flow creates a draft under the homepage, mutates it,
copies it, and deletes both pages so the test site is left as it was.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from rich.console import Console
from rich.json import JSON
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# Check / FlowStep dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    path: str
    method: str = "GET"
    json: Any = None
    # Either a single status code or a tuple of acceptable ones. Some routes
    # legitimately return either 401 or 403 depending on which middleware
    # rejects the request first, and the script shouldn't care.
    expect_status: int | tuple[int, ...] = 200
    assert_json: Callable[[Any], str | None] | None = None
    notes: str = ""
    requires: str | None = None  # "admin" → needs --admin-token or --admin-user
    status: int | None = None
    error: str | None = None
    skipped: bool = False
    body: Any = field(default=None, repr=False)


@dataclass
class FlowStep:
    name: str
    method: str
    path_factory: Callable[[dict[str, Any]], str]
    json_factory: Callable[[dict[str, Any]], Any] = lambda _state: None
    expect_status: int | tuple[int, ...] = 200
    capture_as: str | None = None
    capture_field: str | None = None
    notes: str = ""
    status: int | None = None
    error: str | None = None
    skipped: bool = False
    body: Any = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def has_items(min_count: int) -> Callable[[Any], str | None]:
    def check(body: Any) -> str | None:
        total = body.get("meta", {}).get("total_count")
        if not isinstance(total, int):
            return "missing meta.total_count"
        if total < min_count:
            return f"expected at least {min_count} items, got {total}"
        return None

    return check


def has_field(name: str) -> Callable[[Any], str | None]:
    def check(body: Any) -> str | None:
        if name not in body:
            return f"missing top-level field {name!r}"
        return None

    return check


def has_meta_field(name: str) -> Callable[[Any], str | None]:
    def check(body: Any) -> str | None:
        if name not in body.get("meta", {}):
            return f"missing meta field {name!r}"
        return None

    return check


def is_error_message(body: Any) -> str | None:
    if not isinstance(body, dict) or "message" not in body:
        return "expected error response with a 'message' key"
    return None


# ---------------------------------------------------------------------------
# Standalone checks (public + light auth probes)
# ---------------------------------------------------------------------------


CHECKS: list[Check] = [
    # Reachability of each top-level router ----------------------------------
    Check(
        name="pages list (anonymous)",
        path="/api/v3/pages/",
        assert_json=has_items(1),
    ),
    Check(
        name="images list (anonymous)",
        path="/api/v3/images/",
        assert_json=has_items(0),
    ),
    Check(
        name="documents list (anonymous)",
        path="/api/v3/documents/",
        assert_json=has_items(0),
    ),
    # Pages filtering / fields / search --------------------------------------
    Check(
        name="pages: filter by type",
        path="/api/v3/pages/?type=demosite.BlogEntryPage&fields=title",
        notes="Requires the demosite fixture; type filter mirrors v2 syntax",
    ),
    Check(
        name="pages: pagination",
        path="/api/v3/pages/?limit=2&offset=1",
        assert_json=lambda b: (
            None if len(b.get("items", [])) <= 2 else "limit not respected"
        ),
    ),
    Check(
        name="pages: ordering",
        path="/api/v3/pages/?order=-title",
    ),
    Check(
        name="pages: search",
        path="/api/v3/pages/?search=blog",
        notes="Search backend is wagtail.search; an empty result is still 200",
    ),
    Check(
        name="pages: child_of",
        path="/api/v3/pages/?child_of=2",
    ),
    Check(
        name="pages: descendant_of",
        path="/api/v3/pages/?descendant_of=2",
    ),
    Check(
        name="pages: ?fields=_,title strips defaults",
        path="/api/v3/pages/2/?fields=_,title",
        assert_json=lambda b: (
            None
            if set(b.keys()) == {"title"}
            else f"expected only 'title', got {sorted(b.keys())}"
        ),
        notes="Underscore wipes the default field set",
    ),
    Check(
        name="pages: ?fields=* expands all fields",
        path="/api/v3/pages/2/?fields=*",
        assert_json=has_field("title"),
    ),
    # Detail / find / negative cases -----------------------------------------
    Check(
        name="pages: detail (homepage)",
        path="/api/v3/pages/2/",
        assert_json=has_field("title"),
    ),
    Check(
        name="pages: find by html_path (302)",
        path="/api/v3/pages/find/?html_path=/",
        expect_status=302,
        notes="Mirrors v2: redirects to the canonical detail URL",
    ),
    Check(
        name="pages: unknown id is 404",
        path="/api/v3/pages/999999/",
        expect_status=404,
        assert_json=is_error_message,
    ),
    Check(
        name="pages: bad ?fields value is 400",
        path="/api/v3/pages/?fields=does_not_exist",
        expect_status=400,
        assert_json=is_error_message,
        notes="v3 reuses the v2 BadRequestError → 400 mapping",
    ),
    Check(
        name="pages: bad ?type is 400",
        path="/api/v3/pages/?type=nope.NopePage",
        expect_status=400,
        assert_json=is_error_message,
    ),
    # OpenAPI / docs surface --------------------------------------------------
    Check(
        name="openapi.json is served",
        path="/api/v3/openapi.json",
        assert_json=lambda b: (
            None
            if isinstance(b, dict) and "openapi" in b and "paths" in b
            else "expected an OpenAPI document"
        ),
        notes="Auto-generated by django-ninja",
    ),
    Check(
        name="Swagger UI is served",
        path="/api/v3/docs",
        notes="HTML page; we just check it responds 200",
    ),
    # Anonymous writes are rejected ------------------------------------------
    Check(
        name="POST /pages/ anonymous is rejected",
        path="/api/v3/pages/",
        method="POST",
        json={"type": "demosite.StandardPage", "parent": 2, "title": "x"},
        expect_status=(401, 403),
        notes="No bearer token, no session → 401 or 403 (both are acceptable)",
    ),
    Check(
        name="DELETE /pages/{id} anonymous is rejected",
        path="/api/v3/pages/2/",
        method="DELETE",
        expect_status=(401, 403),
    ),
    # Authenticated standalone probes (run only with --admin-token or session)
    Check(
        name="auth probe: GET /pages/2/ with bearer/session",
        path="/api/v3/pages/2/",
        assert_json=has_field("title"),
        requires="admin",
        notes="Authenticated reads expose all editable fields by default",
    ),
    Check(
        name="auth probe: PATCH non-existent page is 404",
        path="/api/v3/pages/999999/",
        method="PATCH",
        json={"title": "ghost"},
        expect_status=404,
        requires="admin",
    ),
    Check(
        name="auth probe: POST without 'type' is 400",
        path="/api/v3/pages/",
        method="POST",
        json={"parent": 2, "title": "x"},
        expect_status=400,
        requires="admin",
    ),
]


# ---------------------------------------------------------------------------
# Chained admin flow — create / patch / publish / copy / delete
# ---------------------------------------------------------------------------


ADMIN_FLOW: list[FlowStep] = [
    FlowStep(
        name="flow: create draft page under homepage",
        method="POST",
        path_factory=lambda s: "/api/v3/pages/",
        json_factory=lambda s: {
            "type": "demosite.StandardPage",
            "parent": 2,
            "title": "API smoke test page",
            "slug": "api-v3-smoke",
            "body": [],
        },
        expect_status=201,
        capture_as="page",
        capture_field="id",
        notes="Created as draft (live=False, no revision yet published)",
    ),
    FlowStep(
        name="flow: PATCH the draft (rename)",
        method="PATCH",
        path_factory=lambda s: f"/api/v3/pages/{s['page']}/",
        json_factory=lambda s: {"title": "API smoke test page (patched)"},
        expect_status=200,
        notes="Saves a new revision; response includes meta.latest_revision",
    ),
    FlowStep(
        name="flow: publish the draft",
        method="POST",
        path_factory=lambda s: f"/api/v3/pages/{s['page']}/publish/",
        expect_status=200,
        notes="Transitions live=False → live=True",
    ),
    FlowStep(
        name="flow: unpublish",
        method="POST",
        path_factory=lambda s: f"/api/v3/pages/{s['page']}/unpublish/",
        expect_status=200,
    ),
    FlowStep(
        name="flow: copy page (no destination = same parent)",
        method="POST",
        path_factory=lambda s: f"/api/v3/pages/{s['page']}/copy/",
        json_factory=lambda s: {
            "update_attrs": {"slug": "api-v3-smoke-copy", "title": "API smoke copy"},
        },
        expect_status=201,
        capture_as="copy",
        capture_field="id",
    ),
    FlowStep(
        name="flow: delete the copy",
        method="DELETE",
        path_factory=lambda s: f"/api/v3/pages/{s['copy']}/",
        expect_status=204,
    ),
    FlowStep(
        name="flow: delete the original (cleanup)",
        method="DELETE",
        path_factory=lambda s: f"/api/v3/pages/{s['page']}/",
        expect_status=204,
        notes="Test site left exactly as it was found",
    ),
]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def admin_login(client: httpx.Client, username: str, password: str) -> str | None:
    """Log a user in via Wagtail's admin login form for session-auth tests."""
    login_url = "/admin/login/"
    try:
        get_response = client.get(login_url, follow_redirects=False)
    except httpx.HTTPError as exc:
        return f"login GET failed: {exc}"
    if get_response.status_code != 200:
        return f"login page returned HTTP {get_response.status_code}"

    csrf = client.cookies.get("csrftoken")
    if not csrf:
        return "no csrftoken cookie set by login page"

    post_response = client.post(
        login_url,
        data={
            "username": username,
            "password": password,
            "csrfmiddlewaretoken": csrf,
            "next": "/admin/",
        },
        headers={"Referer": str(client.base_url) + login_url},
        follow_redirects=False,
    )
    if post_response.status_code not in (302, 303):
        return f"login POST returned HTTP {post_response.status_code} (expected redirect)"
    if not client.cookies.get("sessionid"):
        return "no sessionid cookie after login"
    return None


def auth_headers(
    client: httpx.Client, path: str, method: str, bearer_token: str | None
) -> dict[str, str]:
    """Build the headers to send for ``method`` against ``path``.

    * Bearer tokens win when present — no CSRF required, no cookies needed.
    * Otherwise, for unsafe methods, the script falls back to the session-auth
      CSRF dance: read ``csrftoken`` from the cookie jar and re-send it as
      ``X-CSRFToken`` (plus a ``Referer`` to satisfy Django's same-origin check).
    """
    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
        return headers
    if method != "GET":
        csrf = client.cookies.get("csrftoken")
        if csrf:
            headers["X-CSRFToken"] = csrf
            headers["Referer"] = str(client.base_url) + path
    return headers


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run(
    check: Check,
    client: httpx.Client,
    show_body: bool,
    authenticated: bool,
    bearer_token: str | None,
) -> None:
    if check.requires == "admin" and not authenticated:
        check.skipped = True
        return

    headers = auth_headers(
        client,
        check.path,
        check.method,
        bearer_token if check.requires == "admin" else None,
    )

    try:
        response = client.request(
            check.method,
            check.path,
            json=check.json if check.method != "GET" else None,
            headers=headers,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        check.error = f"request failed: {exc}"
        return

    check.status = response.status_code
    body: Any = None
    if response.headers.get("content-type", "").startswith("application/json"):
        try:
            body = response.json()
        except ValueError:
            pass

    expected = (
        check.expect_status
        if isinstance(check.expect_status, tuple)
        else (check.expect_status,)
    )
    if response.status_code not in expected:
        check.error = (
            f"expected HTTP {' or '.join(str(s) for s in expected)},"
            f" got {response.status_code}"
        )
        check.body = body
        return

    if check.assert_json is not None:
        if body is None:
            check.error = "response was not JSON"
            return
        check.body = body
        failure = check.assert_json(body)
        if failure is not None:
            check.error = failure
            return
    elif show_body and body is not None:
        check.body = body


def run_flow_step(
    step: FlowStep,
    client: httpx.Client,
    state: dict[str, Any],
    bearer_token: str | None,
) -> None:
    try:
        path = step.path_factory(state)
        payload = step.json_factory(state)
    except KeyError as exc:
        step.skipped = True
        step.notes = f"skipped: missing state {exc.args[0]!r}"
        return

    headers = auth_headers(client, path, step.method, bearer_token)

    try:
        response = client.request(
            step.method, path, json=payload, headers=headers, follow_redirects=False
        )
    except httpx.HTTPError as exc:
        step.error = f"request failed: {exc}"
        return

    step.status = response.status_code
    body: Any = None
    if response.headers.get("content-type", "").startswith("application/json"):
        try:
            body = response.json()
        except ValueError:
            pass

    expected = (
        step.expect_status
        if isinstance(step.expect_status, tuple)
        else (step.expect_status,)
    )
    if response.status_code not in expected:
        step.error = (
            f"expected HTTP {' or '.join(str(s) for s in expected)},"
            f" got {response.status_code}"
        )
        step.body = body
        return
    step.body = body

    if step.capture_as and body is not None:
        if step.capture_field:
            state[step.capture_as] = body.get(step.capture_field)
        else:
            state[step.capture_as] = body


# ---------------------------------------------------------------------------
# Schema demonstration
# ---------------------------------------------------------------------------


def fetch_openapi(client: httpx.Client) -> dict[str, Any] | None:
    """Fetch the auto-generated OpenAPI document or return None on failure."""
    try:
        response = client.get("/api/v3/openapi.json")
    except httpx.HTTPError as exc:
        console.print(f"[red]OpenAPI fetch failed: {exc}[/red]")
        return None
    if response.status_code != 200:
        console.print(
            f"[red]OpenAPI fetch returned HTTP {response.status_code}[/red]"
        )
        return None
    try:
        return response.json()
    except ValueError:
        console.print("[red]OpenAPI response was not JSON[/red]")
        return None


def print_schema_summary(spec: dict[str, Any]) -> None:
    """Render a compact summary of the OpenAPI document."""
    info = spec.get("info", {}) or {}
    console.rule("[bold]OpenAPI schema[/bold]")
    console.print(
        f"  title:       [cyan]{info.get('title', '?')}[/cyan]\n"
        f"  version:     [cyan]{info.get('version', '?')}[/cyan]\n"
        f"  openapi:     [cyan]{spec.get('openapi', '?')}[/cyan]\n"
    )

    paths = spec.get("paths", {}) or {}
    operations: list[tuple[str, str, dict[str, Any]]] = []
    method_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    auth_counts: Counter[str] = Counter()

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "patch", "put", "delete"}:
                continue
            operations.append((method.upper(), path, op))
            method_counts[method.upper()] += 1
            for tag in op.get("tags", []) or ["(no tag)"]:
                tag_counts[tag] += 1
            auth_required = bool(op.get("security"))
            auth_counts["authenticated" if auth_required else "anonymous-ok"] += 1

    summary = Table.grid(padding=(0, 2))
    summary.add_column("metric", style="bold")
    summary.add_column("value")
    summary.add_row("paths", str(len(paths)))
    summary.add_row("operations", str(len(operations)))
    summary.add_row(
        "methods",
        ", ".join(f"{m}: {n}" for m, n in sorted(method_counts.items())),
    )
    summary.add_row(
        "auth split",
        ", ".join(f"{k}: {v}" for k, v in sorted(auth_counts.items())),
    )
    if tag_counts:
        summary.add_row(
            "tags",
            ", ".join(f"{t}: {n}" for t, n in tag_counts.most_common()),
        )
    schemas = spec.get("components", {}).get("schemas", {}) or {}
    summary.add_row("named schemas", str(len(schemas)))
    console.print(summary)

    op_table = Table(title="Operations", show_header=True, header_style="bold")
    op_table.add_column("Method", width=6)
    op_table.add_column("Path", overflow="fold")
    op_table.add_column("operationId")
    op_table.add_column("Auth", width=12)
    for method, path, op in sorted(operations, key=lambda t: (t[1], t[0])):
        op_table.add_row(
            method,
            path,
            op.get("operationId", "?"),
            "required" if op.get("security") else "optional",
        )
    console.print(op_table)

    # Show the request-body schema for POST /pages/ as a concrete example.
    pages_post = paths.get("/pages/", {}).get("post") if isinstance(paths.get("/pages/"), dict) else None
    if pages_post:
        console.rule("Example operation: POST /pages/")
        console.print(JSON.from_data(pages_post))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url",
        default="http://localhost:8001",
        help="Base URL of the test server (default: %(default)s)",
    )
    parser.add_argument(
        "--show-body",
        action="store_true",
        help="Print the decoded JSON body for each successful response",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Only run checks whose name contains this substring",
    )
    parser.add_argument(
        "--admin-token",
        default=None,
        help="Bearer token (ApiToken.key) for write checks. Preferred over --admin-user.",
    )
    parser.add_argument(
        "--admin-user",
        default=None,
        help="Username for session-auth login (fallback when no token is supplied).",
    )
    parser.add_argument(
        "--admin-password",
        default=None,
        help="Password for --admin-user (default: 'changeme' for 'admin', else 'password')",
    )
    parser.add_argument(
        "--skip-flow",
        action="store_true",
        help="Skip the chained write flow (create / patch / publish / copy / delete)",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Only print the OpenAPI summary and exit",
    )
    parser.add_argument(
        "--no-schema",
        action="store_true",
        help="Suppress the OpenAPI summary at the end of the run",
    )
    args = parser.parse_args()

    checks = [c for c in CHECKS if args.filter is None or args.filter in c.name]
    if not checks and not args.schema:
        console.print(f"[red]No checks match filter {args.filter!r}[/red]")
        return 2

    console.print(f"[bold]Wagtail API v3 smoke test[/bold] against {args.base_url}\n")

    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        if args.schema:
            spec = fetch_openapi(client)
            if spec is None:
                return 1
            print_schema_summary(spec)
            return 0

        authenticated = False
        bearer_token: str | None = None

        if args.admin_token:
            bearer_token = args.admin_token
            authenticated = True
            console.print("[green]Using bearer-token auth[/green]\n")
        elif args.admin_user:
            if args.admin_password is not None:
                password = args.admin_password
            elif args.admin_user == "admin":
                password = "changeme"
            else:
                password = "password"
            error = admin_login(client, args.admin_user, password)
            if error is None:
                authenticated = True
                console.print(f"[green]Logged in as {args.admin_user!r}[/green]\n")
            else:
                console.print(
                    f"[red]Admin login failed: {error}. Skipping admin checks.[/red]\n"
                )

        for check in checks:
            run(check, client, args.show_body, authenticated, bearer_token)

        flow_steps: list[FlowStep] = []
        if authenticated and not args.skip_flow:
            flow_steps = [
                step
                for step in ADMIN_FLOW
                if args.filter is None or args.filter in step.name
            ]
            state: dict[str, Any] = {}
            for step in flow_steps:
                run_flow_step(step, client, state, bearer_token)

        # Render results table -------------------------------------------------
        table = Table(show_header=True, header_style="bold")
        table.add_column("Status", width=6)
        table.add_column("HTTP", width=5, justify="right")
        table.add_column("Method", width=6)
        table.add_column("Check")
        table.add_column("Path", overflow="fold")
        table.add_column("Notes / error", overflow="fold")

        passed = failed = skipped = 0

        def add_row(item: Check | FlowStep, path_for_row: str) -> None:
            nonlocal passed, failed, skipped
            method = getattr(item, "method", "POST")
            if item.skipped:
                skipped += 1
                hint = item.notes or "requires --admin-token or --admin-user"
                table.add_row(
                    "[yellow]SKIP[/yellow]", "-", method, item.name, path_for_row, hint
                )
            elif item.error is None:
                passed += 1
                table.add_row(
                    "[green]PASS[/green]",
                    str(item.status or "-"),
                    method,
                    item.name,
                    path_for_row,
                    item.notes,
                )
            else:
                failed += 1
                table.add_row(
                    "[red]FAIL[/red]",
                    str(item.status or "-"),
                    method,
                    item.name,
                    path_for_row,
                    f"[red]{item.error}[/red]",
                )

        for check in checks:
            add_row(check, check.path)
        for step in flow_steps:
            try:
                path_for_row = step.path_factory({})
            except Exception:
                path_for_row = "<resolves at runtime>"
            add_row(step, path_for_row)

        console.print(table)
        total = len(checks) + len(flow_steps)
        console.print(
            f"\n[bold]{passed} passed, {failed} failed, {skipped} skipped[/bold]"
            f" (of {total} checks)"
        )

        if args.show_body:
            console.rule("Response bodies")
            for item in [*checks, *flow_steps]:
                if item.body is None:
                    continue
                label = getattr(item, "path", None) or "<flow step>"
                console.print(f"[bold cyan]{item.name}[/bold cyan]  {label}")
                console.print(JSON.from_data(item.body))
                console.print()

        if not args.no_schema:
            spec = fetch_openapi(client)
            if spec is not None:
                print_schema_summary(spec)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
