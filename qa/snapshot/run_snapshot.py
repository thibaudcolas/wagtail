#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Snapshot test for Wagtail's OpenAPI v2 schema.

Calls ``manage.py spectacular`` in the project's own virtualenv (no
embedded Django imports here), then diffs the result against
``expected_schema.yml`` in this directory.

CI-friendly: exits 0 on match, 1 on drift, with a unified diff printed
to stderr. Pass ``--update`` to refresh the snapshot after an
intentional schema change.

Usage from the repo root::

    ./qa/snapshot/run_snapshot.py
    ./qa/snapshot/run_snapshot.py --update      # refresh the baseline
    ./qa/snapshot/run_snapshot.py --python .venv/bin/python    # custom Python

The script runs ``<python> wagtail/test/manage.py spectacular`` against
the settings module ``snapshot_settings`` (kept in this directory; the
name avoids colliding with ``wagtail/test/settings.py`` which
``manage.py`` puts on ``sys.path[0]``). It loads the resulting YAML and
re-dumps with ``sort_keys=True`` so the snapshot is stable across runs
(drf-spectacular preserves registration order, which is otherwise
sensitive to import side effects).
"""

from __future__ import annotations

import argparse
import difflib
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
EXPECTED = HERE / "expected_schema.yml"
SETTINGS_MODULE = "snapshot_settings"  # see HERE/snapshot_settings.py


def _generate_schema(python_bin: str) -> str:
    """Invoke manage.py spectacular and return the normalized YAML."""
    import yaml

    with tempfile.NamedTemporaryFile(suffix=".yml", delete=False) as fp:
        outfile = pathlib.Path(fp.name)

    try:
        env = os.environ.copy()
        env["DJANGO_SETTINGS_MODULE"] = SETTINGS_MODULE
        # qa/snapshot/ must be on PYTHONPATH so the local ``settings.py``
        # imports as just ``settings``. The repo root stays on PYTHONPATH
        # so the ``wagtail`` package itself imports normally.
        env["PYTHONPATH"] = os.pathsep.join(
            [str(HERE), str(REPO_ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)

        result = subprocess.run(
            [
                python_bin,
                str(REPO_ROOT / "wagtail" / "test" / "manage.py"),
                "spectacular",
                "--file",
                str(outfile),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            sys.stderr.write(
                "\nmanage.py spectacular failed. Check the settings module "
                f"({SETTINGS_MODULE}) and that drf-spectacular is installed.\n"
            )
            sys.exit(result.returncode)

        raw = yaml.safe_load(outfile.read_text())
    finally:
        outfile.unlink(missing_ok=True)

    return yaml.safe_dump(raw, sort_keys=True, default_flow_style=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update",
        action="store_true",
        help="Overwrite the expected snapshot with the freshly generated schema.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help=(
            "Python interpreter to use for manage.py. Defaults to the one "
            "running this script — pass an explicit path if invoking via "
            "uv (eg. --python .venv/bin/python)."
        ),
    )
    args = parser.parse_args()

    # If running under uv-run, the script's own interpreter doesn't have Django.
    # Fall back to the project's .venv/bin/python by default in that case.
    if args.python == sys.executable and "uv" in sys.executable.lower():
        candidate = REPO_ROOT / ".venv" / "bin" / "python"
        if candidate.exists():
            args.python = str(candidate)

    actual = _generate_schema(args.python)

    if args.update or not EXPECTED.exists():
        EXPECTED.write_text(actual)
        rel = EXPECTED.relative_to(REPO_ROOT)
        print(f"Wrote snapshot ({len(actual)} bytes) to {rel}")
        return 0

    expected = EXPECTED.read_text()
    if actual == expected:
        print(f"OK — schema matches snapshot ({EXPECTED.name}).")
        return 0

    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=str(EXPECTED.relative_to(REPO_ROOT)),
            tofile="<generated>",
            n=3,
        )
    )
    sys.stderr.write(diff)
    sys.stderr.write(
        "\nSchema drift detected. If this change is intentional, "
        "refresh the snapshot:\n"
        "    ./qa/snapshot/run_snapshot.py --update\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
