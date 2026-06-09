#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "schemathesis>=3.30",
# ]
# ///
"""Contract-test the Wagtail API v2 against its OpenAPI schema.

Loads the schema from a live Wagtail test server, derives fuzz cases
from it, and checks each response against what the schema promises.

Start the server first (in another terminal):

    ./qa/schemathesis/start_server.sh

Then:

    ./qa/schemathesis/run_schemathesis.py
    ./qa/schemathesis/run_schemathesis.py --max-examples 20   # quicker
    ./qa/schemathesis/run_schemathesis.py --schema-url http://localhost:8001/api/schema/

Exits non-zero on any contract violation (response doesn't match the
schema, response status not in the documented set, …). Authentication-
gated endpoints (the admin API) are filtered out by default since this
prototype tests the unauthenticated public surface.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--schema-url",
        default="http://localhost:8001/api/schema/",
        help="URL of the OpenAPI schema (default: %(default)s).",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8001",
        help="Base URL for generated requests (default: %(default)s).",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=10,
        help=(
            "Number of generated examples per endpoint (default: %(default)s). "
            "Lower for quick smoke runs, higher for nightly runs."
        ),
    )
    parser.add_argument(
        "--include-admin",
        action="store_true",
        help=(
            "Also test the admin API endpoints. They require a session, so "
            "almost every call returns 403 — useful only with an auth hook."
        ),
    )
    args = parser.parse_args()

    import schemathesis
    from hypothesis import settings as hyp_settings

    print(f"Loading schema from {args.schema_url}")
    schema = schemathesis.openapi.from_url(args.schema_url, base_url=args.base_url)

    operations = list(schema.get_all_operations())
    if not args.include_admin:
        operations = [
            op for op in operations if not op.path.startswith("/admin/")
        ]
    print(f"Found {len(operations)} operation(s) to check.")

    test_settings = hyp_settings(max_examples=args.max_examples, deadline=None)

    failures: list[tuple[str, Exception]] = []
    for operation in operations:
        label = f"{operation.method.upper():6s} {operation.path}"

        @test_settings
        @schema.parametrize(endpoint=operation.path)
        def run_case(case):  # type: ignore[no-redef]
            response = case.call()
            case.validate_response(response)

        try:
            run_case()
            print(f"  ok    {label}")
        except Exception as exc:  # noqa: BLE001 — relay everything to the report
            failures.append((label, exc))
            print(f"  FAIL  {label}: {type(exc).__name__}: {exc}")

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for label, exc in failures:
            print(f"  - {label}: {exc}")
        return 1

    print(f"All {len(operations)} operation(s) match the schema.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
