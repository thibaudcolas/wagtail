#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "rich>=13",
# ]
# ///
"""Smoke test for the Wagtail REST API v2 exposed by the test site.

Run the test site first:

    export DJANGO_SETTINGS_MODULE=wagtail.test.settings_ui
    uv run ./wagtail/test/manage.py migrate
    uv run ./wagtail/test/manage.py loaddata wagtail/test/demosite/fixtures/demosite.json
    uv run ./wagtail/test/manage.py shell -c "
    from django.contrib.auth import get_user_model
    from wagtail.models import Page, Site
    U = get_user_model()
    u, _ = U.objects.update_or_create(username='admin', defaults={
        'email': 'admin@example.com', 'is_staff': True, 'is_superuser': True,
    })
    u.set_password('changeme'); u.save()
    # Public API site routing keys off Host; map localhost:8001 to the home page.
    Site.objects.update_or_create(
        hostname='localhost', port=8001,
        defaults={'root_page': Page.objects.get(pk=2)},
    )
    "
    uv run ./wagtail/test/manage.py runserver 0:8001

Then:

    ./test_api_v2.py                       # public-only checks
    ./test_api_v2.py --admin-user admin    # adds admin + action demos
    ./test_api_v2.py --show-body           # dump JSON for each response
    ./test_api_v2.py --filter publish      # run a subset by name substring

By default `--admin-user admin` logs in as `admin:changeme` (matches the
snippet above). Override with `--admin-password` if your fixtures differ.

The admin login flow follows the regular session-auth Wagtail admin: GET the
login form to seed `csrftoken`, POST credentials, then send the `csrftoken`
cookie value as `X-CSRFToken` on every state-changing request.

Admin-action checks run as a *chained flow* that copies the homepage, locks
and unlocks it, attempts a move, then deletes the temporary copy — so the
test site is left exactly as it was. Individual standalone admin checks run
before the flow.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from rich.console import Console
from rich.json import JSON
from rich.table import Table

console = Console()


@dataclass
class Check:
    name: str
    path: str
    method: str = "GET"
    json: Any = None
    expect_status: int = 200
    # Optional assertion run against the decoded JSON body. Return None for pass
    # or a string describing the failure.
    assert_json: Callable[[Any], str | None] | None = None
    notes: str = ""
    # When set, the check is only executed if the client passes this requirement
    # (currently just "admin" to mean "needs a logged-in admin session").
    requires: str | None = None
    # Populated after running.
    status: int | None = None
    error: str | None = None
    skipped: bool = False
    body: Any = field(default=None, repr=False)


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


CHECKS: list[Check] = [
    # Endpoint reachability ----------------------------------------------------
    Check(
        name="pages list",
        path="/api/main/pages/",
        assert_json=has_items(1),
    ),
    Check(
        name="images list",
        path="/api/main/images/",
        assert_json=has_items(0),
    ),
    Check(
        name="documents list",
        path="/api/main/documents/",
        assert_json=has_items(0),
    ),
    Check(
        name="redirects list",
        path="/api/main/redirects/",
        assert_json=has_items(0),
    ),
    # Pages: filters, fields, search ------------------------------------------
    Check(
        name="pages: filter by type",
        path="/api/main/pages/?type=demosite.BlogEntryPage&fields=title,date,tags",
        notes="requires demosite fixture",
    ),
    Check(
        name="pages: pagination",
        path="/api/main/pages/?limit=2&offset=1",
        assert_json=lambda b: (
            None if len(b.get("items", [])) <= 2 else "limit not respected"
        ),
    ),
    Check(
        name="pages: ordering",
        path="/api/main/pages/?type=demosite.BlogEntryPage&order=-date&fields=title,date",
    ),
    Check(
        name="pages: search",
        path="/api/main/pages/?search=blog",
    ),
    Check(
        name="pages: child_of",
        path="/api/main/pages/?child_of=2",
    ),
    Check(
        name="pages: descendant_of",
        path="/api/main/pages/?descendant_of=2",
    ),
    # Detail views -------------------------------------------------------------
    Check(
        name="pages: detail (root home)",
        path="/api/main/pages/2/",
        assert_json=has_field("title"),
    ),
    Check(
        name="pages: detail with custom fields",
        path="/api/main/pages/2/?fields=title,show_in_menus,search_description",
        assert_json=has_meta_field("show_in_menus"),
        notes="show_in_menus is a meta field, not top-level",
    ),
    Check(
        name="pages: find by html_path (redirects)",
        path="/api/main/pages/find/?html_path=/",
        expect_status=302,
    ),
    # Negative cases -----------------------------------------------------------
    Check(
        name="pages: unknown id is 404",
        path="/api/main/pages/999999/",
        expect_status=404,
    ),
    Check(
        name="api root has no index (404 expected)",
        path="/api/main/",
        expect_status=404,
        notes="WagtailAPIRouter does not expose an index view",
    ),
    # Admin API: anonymous access should be rejected -------------------------
    # The Wagtail admin URL conf wraps these in require_admin_access(), which
    # redirects unauthenticated requests to /admin/login/ rather than 403'ing.
    Check(
        name="admin api: anonymous list redirects to login",
        path="/admin/api/main/pages/",
        expect_status=302,
        requires="anonymous",
        notes="require_admin_access decorates the admin URL tree",
    ),
    Check(
        name="admin api: anonymous action redirects to login",
        path="/admin/api/main/pages/2/action/publish/",
        method="POST",
        json={},
        expect_status=302,
        requires="anonymous",
        notes="POST /admin/api/main/pages/<id>/action/<name>/ — session required",
    ),
    # Admin API: authenticated standalone checks (only with --admin-user) ----
    Check(
        name="admin api: authenticated list",
        path="/admin/api/main/pages/",
        assert_json=has_field("items"),
        requires="admin",
        notes="Returns drafts/aliases too, with admin-only fields like status",
    ),
    Check(
        name="admin api: authenticated detail (homepage)",
        path="/admin/api/main/pages/2/",
        assert_json=has_field("title"),
        requires="admin",
        notes="Page 2 is the demosite homepage",
    ),
    Check(
        name="admin api: action with invalid serializer payload",
        path="/admin/api/main/pages/2/action/move/",
        method="POST",
        json={},
        expect_status=400,
        requires="admin",
        notes="destination_page_id is required on MovePageAPIActionSerializer",
    ),
    Check(
        name="admin api: unknown action name is 404",
        path="/admin/api/main/pages/2/action/does-not-exist/",
        method="POST",
        json={},
        expect_status=404,
        requires="admin",
        notes="action_view raises Http404 for unknown action names",
    ),
    Check(
        name="admin api: action 'publish' on homepage (idempotent)",
        path="/admin/api/main/pages/2/action/publish/",
        method="POST",
        json={},
        assert_json=has_field("title"),
        requires="admin",
        notes="Idempotent: re-publishes the latest revision",
    ),
]


# Chained admin flow ---------------------------------------------------------
#
# Each step may reference state captured by previous ones via path_factory or
# json_factory. Used to exercise the destructive actions safely: we copy the
# homepage, manipulate the copy, then delete it.

@dataclass
class FlowStep:
    name: str
    method: str
    path_factory: Callable[[dict[str, Any]], str]
    json_factory: Callable[[dict[str, Any]], Any] = lambda _state: {}
    expect_status: int = 200
    capture_as: str | None = None  # store response JSON under state[key]
    capture_field: str | None = None  # if set, capture only state[key] = body[field]
    notes: str = ""
    status: int | None = None
    error: str | None = None
    skipped: bool = False
    body: Any = field(default=None, repr=False)
    resolved_path: str = "<unresolved>"


ADMIN_FLOW: list[FlowStep] = [
    FlowStep(
        name="flow: copy homepage to temp page",
        method="POST",
        path_factory=lambda s: "/admin/api/main/pages/2/action/copy/",
        json_factory=lambda s: {
            "recursive": False,
            "keep_live": False,
            "slug": "api-smoke-test-copy",
        },
        expect_status=201,
        capture_as="copy",
        capture_field="id",
        notes="Creates a draft copy under the same parent",
    ),
    FlowStep(
        name="flow: lock the temp page (idempotent)",
        method="POST",
        path_factory=lambda s: f"/admin/api/main/pages/{s['copy']}/action/lock/",
        notes="Hook-extensible built-in action",
    ),
    FlowStep(
        name="flow: lock again (still 200)",
        method="POST",
        path_factory=lambda s: f"/admin/api/main/pages/{s['copy']}/action/lock/",
        notes="Repeat call exercises the idempotent branch",
    ),
    FlowStep(
        name="flow: unlock the temp page",
        method="POST",
        path_factory=lambda s: f"/admin/api/main/pages/{s['copy']}/action/unlock/",
    ),
    FlowStep(
        name="flow: publish the temp page",
        method="POST",
        path_factory=lambda s: f"/admin/api/main/pages/{s['copy']}/action/publish/",
        notes="Transitions draft → live",
    ),
    FlowStep(
        name="flow: unpublish the temp page",
        method="POST",
        path_factory=lambda s: f"/admin/api/main/pages/{s['copy']}/action/unpublish/",
        json_factory=lambda s: {"recursive": False},
    ),
    FlowStep(
        name="flow: create_alias of the temp page",
        method="POST",
        path_factory=lambda s: f"/admin/api/main/pages/{s['copy']}/action/create_alias/",
        json_factory=lambda s: {
            "recursive": False,
            "update_slug": "api-smoke-test-alias",
        },
        expect_status=201,
        capture_as="alias",
        capture_field="id",
        notes="Returns the new alias page",
    ),
    FlowStep(
        name="flow: delete the alias",
        method="POST",
        path_factory=lambda s: f"/admin/api/main/pages/{s['alias']}/action/delete/",
        expect_status=204,
    ),
    FlowStep(
        name="flow: delete the temp page (cleanup)",
        method="POST",
        path_factory=lambda s: f"/admin/api/main/pages/{s['copy']}/action/delete/",
        expect_status=204,
        notes="Test site left exactly as it was found",
    ),
]


def admin_login(client: httpx.Client, username: str, password: str) -> str | None:
    """Log a user in via Wagtail's admin login form. Returns an error string or None."""
    login_url = "/admin/login/"
    try:
        # GET the login page to seed the csrftoken cookie.
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
    # Successful Wagtail admin login redirects (302) to "next".
    if post_response.status_code not in (302, 303):
        return f"login POST returned HTTP {post_response.status_code} (expected redirect)"
    if not client.cookies.get("sessionid"):
        return "no sessionid cookie after login"
    return None


def run_flow_step(
    step: FlowStep,
    client: httpx.Client,
    state: dict[str, Any],
) -> None:
    """Execute a chained admin flow step, capturing state for follow-ups."""
    try:
        path = step.path_factory(state)
        payload = step.json_factory(state)
    except KeyError as exc:
        # A previous step failed to capture required state; skip cleanly.
        step.skipped = True
        step.notes = f"skipped: missing state {exc.args[0]!r}"
        return
    step.resolved_path = path

    headers: dict[str, str] = {}
    csrf = client.cookies.get("csrftoken")
    if csrf:
        headers["X-CSRFToken"] = csrf
        headers["Referer"] = str(client.base_url) + path

    try:
        response = client.request(
            step.method, path, json=payload, headers=headers, follow_redirects=False
        )
    except httpx.HTTPError as exc:
        step.error = f"request failed: {exc}"
        return

    step.status = response.status_code
    if response.status_code != step.expect_status:
        step.error = f"expected HTTP {step.expect_status}, got {response.status_code}"
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                step.body = response.json()
            except ValueError:
                pass
        return

    if response.headers.get("content-type", "").startswith("application/json"):
        try:
            step.body = response.json()
        except ValueError:
            pass

    if step.capture_as and step.body is not None:
        if step.capture_field:
            state[step.capture_as] = step.body.get(step.capture_field)
        else:
            state[step.capture_as] = step.body


def run(check: Check, client: httpx.Client, show_body: bool, authenticated: bool) -> None:
    if check.requires == "admin" and not authenticated:
        check.skipped = True
        return

    headers: dict[str, str] = {}
    if check.method != "GET":
        csrf = client.cookies.get("csrftoken")
        if csrf:
            headers["X-CSRFToken"] = csrf
            headers["Referer"] = str(client.base_url) + check.path

    try:
        # follow_redirects=False so the /find/ check observes the 302 itself.
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
    if response.status_code != check.expect_status:
        check.error = f"expected HTTP {check.expect_status}, got {response.status_code}"
        # Capture body to aid debugging when the status is unexpected.
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                check.body = response.json()
            except ValueError:
                pass
        return

    if check.assert_json is not None:
        try:
            body = response.json()
        except ValueError:
            check.error = "response was not JSON"
            return
        check.body = body
        failure = check.assert_json(body)
        if failure is not None:
            check.error = failure
            return
    elif show_body and response.headers.get("content-type", "").startswith(
        "application/json"
    ):
        check.body = response.json()


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
        "--admin-user",
        default=None,
        help="Username for admin login. Required to exercise /admin/api/ action endpoints.",
    )
    parser.add_argument(
        "--admin-password",
        default=None,
        help="Password for the admin user (default: 'changeme' for --admin-user 'admin', "
        "'password' otherwise — matches Wagtail's bundled test fixtures)",
    )
    parser.add_argument(
        "--skip-flow",
        action="store_true",
        help="Skip the chained admin flow (copy/move/delete cycle)",
    )
    args = parser.parse_args()

    checks = [c for c in CHECKS if args.filter is None or args.filter in c.name]
    if not checks:
        console.print(f"[red]No checks match filter {args.filter!r}[/red]")
        return 2

    console.print(f"[bold]Wagtail API v2 smoke test[/bold] against {args.base_url}\n")

    flow_steps: list[FlowStep] = []
    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        authenticated = False

        # Run anonymous checks first (before any session cookies are set).
        anonymous_checks = [c for c in checks if c.requires == "anonymous"]
        regular_checks = [c for c in checks if c.requires != "anonymous"]

        for check in anonymous_checks:
            run(check, client, args.show_body, authenticated)

        if args.admin_user:
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

        for check in regular_checks:
            run(check, client, args.show_body, authenticated)

        if authenticated and not args.skip_flow:
            flow_steps = [
                step
                for step in ADMIN_FLOW
                if args.filter is None or args.filter in step.name
            ]
            state: dict[str, Any] = {}
            for step in flow_steps:
                run_flow_step(step, client, state)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", width=6)
    table.add_column("HTTP", width=5, justify="right")
    table.add_column("Method", width=6)
    table.add_column("Check")
    table.add_column("Path", overflow="fold")
    table.add_column("Notes / error", overflow="fold")

    passed = 0
    failed = 0
    skipped = 0

    def add_row(item, path_for_row: str) -> None:
        nonlocal passed, failed, skipped
        if item.skipped:
            skipped += 1
            table.add_row(
                "[yellow]SKIP[/yellow]",
                "-",
                getattr(item, "method", "POST"),
                item.name,
                path_for_row,
                item.notes or "requires --admin-user",
            )
        elif item.error is None:
            passed += 1
            table.add_row(
                "[green]PASS[/green]",
                str(item.status or "-"),
                getattr(item, "method", "POST"),
                item.name,
                path_for_row,
                item.notes,
            )
        else:
            failed += 1
            table.add_row(
                "[red]FAIL[/red]",
                str(item.status or "-"),
                getattr(item, "method", "POST"),
                item.name,
                path_for_row,
                f"[red]{item.error}[/red]",
            )

    for check in checks:
        add_row(check, check.path)

    for step in flow_steps:
        add_row(step, step.resolved_path)

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

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
