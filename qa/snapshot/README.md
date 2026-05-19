# QA prototype: schema snapshot testing

Catches accidental changes to the generated OpenAPI schema by diffing
the live output against a committed reference (`expected_schema.yml`).
Same idea drf-spectacular and FastAPI use in their own test suites.

## What's here

```
qa/snapshot/
├── README.md
├── run_snapshot.py        # the runner — generates schema and diffs
├── snapshot_settings.py   # Django settings with drf-spectacular + schema ext
└── expected_schema.yml    # the committed snapshot (regenerate with --update)
```

`snapshot_settings.py` extends `wagtail.test.settings` with
`drf_spectacular` and `wagtail.api.v2.schema` in `INSTALLED_APPS`. The
module is named `snapshot_settings` (not `settings`) to avoid colliding
with `wagtail/test/settings.py` — `manage.py` puts that directory on
`sys.path[0]`.

## Running

From the repo root, with the project venv set up:

```sh
./qa/snapshot/run_snapshot.py                    # compare to baseline
./qa/snapshot/run_snapshot.py --update           # refresh the baseline
./qa/snapshot/run_snapshot.py --python .venv/bin/python   # explicit interpreter
```

Exit codes:

- `0` — schema matches the snapshot (or baseline was just created)
- `1` — schema drift; unified diff is printed to stderr
- non-zero subprocess code — `manage.py spectacular` itself failed

The script invokes `manage.py spectacular --file <tmp>` in a
subprocess using the project venv, then loads the YAML and re-dumps it
with `sort_keys=True` for stable comparisons across runs.

## How it would fit in CI

Single step, no extra services:

```yaml
- name: Schema snapshot
  run: ./qa/snapshot/run_snapshot.py
```

A drift makes the job fail with a readable diff. Refresh the snapshot
intentionally in the PR that changes the schema:

```sh
./qa/snapshot/run_snapshot.py --update
git add qa/snapshot/expected_schema.yml
```

## Maintenance notes

- Snapshots are noisy across drf-spectacular minor releases (component
  name tweaks, type narrowing). Pin drf-spectacular in your test deps
  and bump it deliberately.
- Don't commit the snapshot until you've reviewed it once — it's a
  contract with API consumers.
- The snapshot is sorted by key, so swap-only reorderings stay quiet.
  Real semantic changes (new endpoints, removed fields, parameter
  changes) still show up clearly in the diff.
