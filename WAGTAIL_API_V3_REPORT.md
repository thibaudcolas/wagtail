# Wagtail API v3 — implementation report

This report documents the introduction of `wagtail.api.v3`, a new read/write
REST API for Wagtail built on [Django Ninja](https://github.com/vitalik/django-ninja).
It is intended as a starting point for a community-grade v3, not a finished
product, and is structured so subsequent phases (images/documents/snippets
writes, OpenAPI polish, auth-token admin UI) can be layered in without
disturbing the foundations laid here.

---

## 1. Goals

### 1.1 As you expressed them

- Introduce a **v3** of the Wagtail REST API, built on **Django Ninja**.
- Provide **both read and write** operations, similar to
  [`wagtail-write-api`](https://github.com/tomdyson/wagtail-write-api) (a copy
  is vendored under `wagtail-write-api/` for reference).
- **Keep the v2 API as-is**, side-by-side with v3.
- Use the existing demo site (`DJANGO_SETTINGS_MODULE=wagtail.test.settings_ui`,
  `runserver 0:8003`) for ad-hoc testing; update `wagtail/test/urls.py`
  so both APIs are reachable simultaneously.

### 1.2 My interpretation

The literal asks above leave a number of judgements open. My reading of the
spirit behind them is:

1. **v3 should feel like part of Wagtail core, not a third-party graft.**
   That means following Wagtail's existing conventions where possible
   (`api_fields`, `APIField`, `PagePermissionPolicy`, the
   `{"meta": {...}, "items": [...]}` envelope), rather than re-doing them in
   wagtail-write-api's idiom. New code should be discoverable to authors who
   already know v2.
2. **wagtail-write-api is inspiration, not blueprint.** It is a third-party
   project with a different set of constraints (standalone app, no obligation
   to match v2's quirks). For a core-shipped v3, several of its choices
   warrant revisiting — most notably its blanket exposure of all editable
   fields and its custom token model that lives outside Wagtail's permission
   policies.
3. **The deliverable is a foundation, not parity with the reference.**
   wagtail-write-api ships pages + images + snippets writes, schema discovery,
   rich-text conversion, and revisions out of the box. Building all of that
   in a single change would be too large to land cleanly. The right shape for
   this commit is: page reads + page writes complete, images/documents reads
   stubbed (writes deferred), snippets deferred, with the architecture chosen
   so each later phase is additive.
4. **Backwards compatibility for v2 is non-negotiable.** Authors who currently
   subclass `PagesAPIViewSet`, register a `WagtailAPIRouter`, or rely on the
   v2 response shape must not be affected. Verified: all 320 v2 tests pass.

---

## 2. What was built

### 2.1 Package layout

```
wagtail/api/v3/
├── __init__.py
├── apps.py                 # AppConfig (label: wagtailapi_v3)
├── api.py                  # build_api() factory: NinjaAPI + exception handlers
├── auth.py                 # BearerTokenAuth, resolve_optional_user(), write_auth()
├── filters.py              # ?type ?child_of ?ancestor_of ?descendant_of ?locale
│                           # ?translation_of ?site ?search ?order + exact-match
├── models.py               # ApiToken model
├── pagination.py           # offset/limit → v2-compatible envelope
├── permissions.py          # thin wrappers over PagePermissionPolicy
├── schemas.py              # field exposure + read serialisation + write payload apply
├── utils.py                # parse_fields_parameter, page_models_from_string,
│                           # BadRequestError
├── migrations/
│   ├── __init__.py
│   └── 0001_initial.py     # ApiToken table
├── endpoints/
│   ├── __init__.py
│   ├── pages.py            # full CRUD + publish/unpublish/copy/move
│   ├── images.py           # read-only stub
│   └── documents.py        # read-only stub
└── tests/
    ├── __init__.py
    └── test_pages.py       # 7 tests; covers anon read, ?fields, ?type,
                            # token-auth create/patch/publish/delete
```

### 2.2 Endpoints

All routes are mounted at `/api/v3/`. They are listed in the auto-generated
Swagger UI at `/api/v3/docs` and as machine-readable OpenAPI at
`/api/v3/openapi.json`.

| Method | Path                                | Auth        | Description                          |
| ------ | ----------------------------------- | ----------- | ------------------------------------ |
| GET    | `/pages/`                           | optional    | List pages (filterable, paginated)   |
| GET    | `/pages/find/`                      | optional    | Redirect to detail by id or html_path |
| GET    | `/pages/{id}/`                      | optional    | Page detail                          |
| POST   | `/pages/`                           | required    | Create a draft page under a parent   |
| PATCH  | `/pages/{id}/`                      | required    | Update fields; saves a new revision  |
| DELETE | `/pages/{id}/`                      | required    | Delete page                          |
| POST   | `/pages/{id}/publish/`              | required    | Save revision + publish              |
| POST   | `/pages/{id}/unpublish/`            | required    | Unpublish                            |
| POST   | `/pages/{id}/copy/`                 | required    | Copy to destination (or same parent) |
| POST   | `/pages/{id}/move/`                 | required    | Move under destination               |
| GET    | `/images/`, `/images/{id}/`         | optional    | Read-only listing/detail             |
| GET    | `/documents/`, `/documents/{id}/`   | optional    | Read-only listing/detail             |

"Optional auth" means: anonymous callers get the v2-style `api_fields`
subset; authenticated callers (session cookie or bearer token) get all
editable fields. "Required auth" means: 401 if no bearer token or session
cookie is present.

### 2.3 Auth model

A new `ApiToken(user, key, label, created, last_used, revoked)` model holds
bearer tokens (`secrets.token_urlsafe(32)`). Presented as
`Authorization: Bearer <key>`. The `write_auth()` chain accepts either a
bearer token *or* an authenticated Django session, so the same endpoints
work for browser-based admin tooling and for programmatic clients.

Permissions delegate to `PagePermissionPolicy` (Wagtail's existing tree-aware
policy). There is no separate v3 permission system; we reuse what the admin
already enforces.

### 2.4 Field exposure rules

| Caller          | Visible fields                                                |
| --------------- | ------------------------------------------------------------- |
| Anonymous       | Common page fields + each model's declared `api_fields`       |
| Authenticated   | Common page fields + all editable model fields (minus a small protected set: `path`, `depth`, `numchild`, `live_revision`, etc.) |

Both honour the v2 `?fields=` grammar (`?fields=*` for all, `?fields=_,title`
for none-then-just-title, `?fields=body,-search_description` for additive
plus subtractive).

### 2.5 Integration touch-points

- `pyproject.toml`: `django-ninja>=1.4,<2.0` added to runtime deps.
- `wagtail/test/settings.py`: `wagtail.api.v3` appended to `INSTALLED_APPS`
  immediately after `wagtail.api.v2`.
- `wagtail/test/urls.py`: `path("api/v3/", v3_api.urls)` added next to the
  existing v2 mount at `/api/main/`.

No changes to v2, no changes to non-test files outside `wagtail/api/v3/`,
no shared imports leaking back into v2.

### 2.6 Verification

- v3 test suite: 7/7 pass
- v2 test suite (regression): 320/320 pass
- Live runserver: `/api/main/pages/` and `/api/v3/pages/` both return 200
  with structurally compatible payloads; `/api/v3/docs` and
  `/api/v3/openapi.json` render

---

## 3. Design decisions

For each decision below: the question, the options I considered, how I
weighed them, and what I chose. Decisions are ordered roughly by
architectural impact (most impactful first).

### 3.1 Where should v3 live in the source tree?

**Question.** Inside `wagtail/api/` next to v2, or in a new top-level
location?

**Options.**
- A. `wagtail/api/v3/` (sibling of `wagtail/api/v2/`).
- B. `wagtail/api/v3/` plus moving `wagtail/api/v2/` into `wagtail/api/legacy/`
  so v3 becomes "the API."
- C. A separate top-level package (e.g. `wagtail/ninja_api/`) signalling that
  it's a different stack technologically.

**Assessment.** Option B is a breaking change to import paths and goes far
beyond the scope of "introduce v3." Option C makes the technology stack
visible in the import path but obscures the natural progression (`v2 → v3`)
for authors. The convention `wagtail/api/v2/` already exists; mirroring it
is least surprising.

**Chosen: A.** The package label (`wagtailapi_v3`) follows the same scheme
as v2 (`wagtailapi_v2`). Tests, migrations, and AppConfig all sit alongside
the v2 equivalents.

### 3.2 How are field exposure rules decided per request?

**Question.** Which fields should a v3 response include by default for which
caller?

This is the single most consequential design decision, so I asked you about
it explicitly. The candidate options I presented were:

- A. Reuse v2 `api_fields` (whitelist) for everyone.
- B. Auto-discover all editable fields, opt-out via `write_api_exclude`
  (wagtail-write-api's approach).
- C. **Hybrid:** `api_fields` for anonymous reads, all editable fields for
  authenticated reads and writes.

**Assessment.**
- A is the safest but means every model must be re-annotated before it's
  useful for the write API — a tax on adoption.
- B is the lowest-friction for authors but risks accidentally exposing
  fields that shouldn't be public (especially on third-party page
  packages whose authors never opted in).
- C splits the concern along the boundary that already matters: public
  versus authenticated access. Public consumers stay locked down (matching
  v2's mental model); admin-style clients see what they need without each
  field having to be declared twice.

**Chosen: C** (per your preference). Implemented in
`schemas.get_available_fields(model, *, authenticated, is_page)`. The
authenticated branch enumerates concrete model fields and strips an
explicit protected set (`path`, `depth`, `numchild`, internal Wagtail
flags). The anonymous branch uses `getattr(model, "api_fields", ())`.

This decision deserves to be revisited if v3 ever exposes anonymous writes
(currently impossible), or if there's appetite to add `api_write_fields`
analogous to `api_fields`.

### 3.3 Read responses: pre-generated Pydantic schemas or dynamic dicts?

**Question.** Should I declare a Pydantic `Schema` per Wagtail page model
(à la wagtail-write-api's `SchemaRegistry`), or return plain `dict`s and
skip strict response typing?

**Options.**
- A. Auto-generate a `ReadSchema` per concrete page model at startup and
  declare it as the response type.
- B. Return `dict[str, Any]`, document the shape elsewhere.
- C. A single hand-written `PageRead` schema covering common fields, with
  custom fields nested under a free-form `body` dict.

**Assessment.** The response shape is **not stable per-model** in v3 — it
depends on the caller's auth state and the `?fields=` parameter, both of
which can add or remove top-level keys. A Pydantic schema implies a fixed
shape; declaring one would make `/openapi.json` lie about reality. Option C
is the worst of both worlds: it documents *something*, but not what clients
actually receive. Option A's machinery (introspecting fields, recursing into
Orderables, mapping Django types to Pydantic types) is several hundred lines
of code that mostly exists to enable strict typing that the dynamic
behaviour then undermines.

**Chosen: B.** Returns are `OrderedDict`s assembled in `schemas.serialize_instance`.
The trade-off is honest: the OpenAPI spec shows `object` rather than
field-by-field types. This is the single biggest area of investment left
for a follow-up — generating *accurate* per-model schemas (using the
`?fields=` parameter as a discriminated union, or splitting "anonymous"
vs "authenticated" into separate operations) would meaningfully improve
the developer experience but is substantial work in its own right.

### 3.4 Write payloads: typed Pydantic schemas or `dict`?

**Question.** Same question, applied to request bodies.

**Options.**
- A. Auto-generate a `CreateSchema` / `PatchSchema` per model.
- B. Accept `dict` for both, validate field-by-field against the model.

**Assessment.** The case for A is stronger here than for reads — request
bodies *should* be well-defined and validated up-front. But it inherits all
of wagtail-write-api's complexity around StreamField (every block type would
need its own schema), rich text (multi-format negotiation), Orderables
(nested schemas with `sort_order`), and choosers (FK ids vs nested objects).
Doing it well is a project; doing it badly produces validation errors that
are less helpful than Django's own model-level errors.

**Chosen: B.** `schemas.apply_payload(instance, payload)` walks the payload
keys, checks each against `model._meta.get_field(name)`, applies, and
reports `applied`/`skipped` field lists in the response so clients can see
what landed. StreamField values pass through as `list[dict]` (Wagtail's
storage format); rich text passes through as raw HTML containing
Wagtail-internal `<embed>`/`<a linktype=…>` tags (per your preference,
3.7). `WRITE_PROTECTED` blacklists internal fields (`path`, `depth`,
`numchild`, `live`, `latest_revision`, etc.) that callers must not be able
to set directly.

Same caveat as 3.3: this is the right shape for now but explicit per-model
schemas are a worthwhile follow-up.

### 3.5 Authentication

**Question.** What auth should v3 ship by default?

**Options.**
- A. Django session only.
- B. **Django session + a new `ApiToken` model.**
- C. Pluggable hooks only; no default token model.

**Assessment** (per your answer to my question): A is too thin for headless
CMS clients; C punts the work to every consumer of v3 and means there's no
canonical answer for "how do I authenticate from a script." B follows
wagtail-write-api's lead but uses Wagtail's existing permission policies
rather than introducing a parallel permission system.

**Chosen: B.** `ApiToken` lives in `wagtail.api.v3.models`; tokens are
generated via `secrets.token_urlsafe(32)` and validated by
`BearerTokenAuth`. Token rows track `created`, `last_used`, `revoked`, and
`label`. Crucially, the token only *identifies* the user — every operation
then runs through `PagePermissionPolicy`, so a token never grants more
than the underlying user has.

Deferred: an admin UI for creating tokens. For now they are created via
`ApiToken.objects.create(user=…, label=…)` in code or the Django admin.

### 3.6 Read endpoints with optional authentication

**Question.** How do I let the same route serve both anonymous and
authenticated callers, with different field visibility?

**Options.**
- A. Two operations: `GET /pages/` (anon) and `GET /pages/me/` (authed).
- B. A custom `Auth` class that returns `AnonymousUser` on failure so the
  route is never rejected.
- C. `auth=None` on the route, plus a `resolve_optional_user(request)`
  helper that inspects the bearer header / session and reshapes the
  response accordingly.

**Assessment.** A is the most explicit but fragments documentation and
client code (every URL has two flavours). B works but Django Ninja's
session auth machinery wasn't designed for that — it short-circuits in a
way that interacts awkwardly with bearer-auth fallback. C is the simplest:
the route is unconditionally public, and a helper turns "is there a usable
bearer token or session?" into a boolean and a `user` that the response
serialiser can consult.

**Chosen: C.** `auth.resolve_optional_user(request)` checks for an
`Authorization: Bearer <key>` header first, then falls back to
`request.user` set by Django's `AuthenticationMiddleware`. Returns `None`
for anonymous, the user otherwise. Each read endpoint calls it once and
passes `authenticated=` into `serialize_page`.

### 3.7 Rich text representation

**Question.** What format should rich-text fields be in v3 responses and
requests?

**Options.**
- A. **Wagtail internal format** (raw DB value, with `<embed>` / `<a linktype>` tags).
- B. Expanded HTML by default (`expand_db_html`), raw on `?format=raw`.
- C. Multi-format like wagtail-write-api (Markdown ↔ HTML ↔ Wagtail
  internal, controlled by an `Accept` header or `?format=` parameter).

**Assessment** (per your answer): C is the most flexible but adds a
converter module (~150 lines in the reference project) and a class of
edge cases (round-tripping `wagtail://page/N` links from arbitrary
Markdown). B's appeal is friendliness to headless clients but introduces a
write/read asymmetry — write API authors would have to learn that the read
format isn't safe to round-trip through write. A is the simplest and the
honest baseline.

**Chosen: A.** Reads return the raw stored value. Writes accept it
verbatim. A future v3 enhancement can add an opt-in `?format=html`
parameter without breaking any existing client.

### 3.8 Router architecture

**Question.** How should authors register custom endpoints?

**Options.**
- A. Replicate v2's `WagtailAPIRouter` API — a registry of endpoint classes
  keyed by name, exposing `register_endpoint("posts", PostsViewSet)`.
- B. Expose Django Ninja's native composition: `api.add_router("/posts/", router)`.
- C. Both: a thin wrapper that lets v2-style consumers register a class and
  internally builds the Ninja router.

**Assessment.** A is familiar to current Wagtail v2 authors but
fundamentally mismatched with Django Ninja's model — Ninja routes are
functions on a router, not methods on a class with `as_view()`. Bridging
the two would mean re-implementing v2's viewset machinery (filter
backends, action dispatch, serializer classes) on top of Ninja, which
defeats the point of adopting a new framework. C is the worst case: it
ships the bridge *and* the native API, doubling the surface area.

**Chosen: B.** `build_api()` returns the `NinjaAPI` instance; authors who
want to extend it call `api.add_router("/my-endpoint/", my_router)` before
mounting. There is no class-based equivalent. This is a deliberate break
from v2's customisation idiom and worth calling out in docs.

### 3.9 Pagination response shape

**Question.** Should v3 use Django Ninja's built-in pagination (which
returns `{"count": N, "next": …, "previous": …, "items": [...]}`) or match
v2's `{"meta": {"total_count": N}, "items": [...]}`?

**Options.**
- A. Use Django Ninja's `paginate` decorator with its default shape.
- B. Write a small custom paginator that emits v2's shape.

**Assessment.** Migration from v2 to v3 is one of the explicit goals.
Forcing every consumer to rewrite their pagination handling is gratuitous
churn. The v2 shape is also slightly more honest — `total_count` is
unambiguous about being the *unpaginated* total, where Django Ninja's
`count` looks like the page length to careless readers.

**Chosen: B.** `pagination.paginate(queryset, request)` slices by
`?offset` / `?limit` and returns `(items, total_count)`. Endpoints
assemble the envelope. Identical to v2 verbatim, including the
`WAGTAILAPI_LIMIT_MAX` setting (default 20).

### 3.10 Permission checks

**Question.** Where do permission checks live and what enforces them?

**Options.**
- A. Re-implement permission rules inline in each endpoint.
- B. Delegate to `PagePermissionPolicy` (Wagtail's existing tree-aware
  policy used by the admin).
- C. Build a separate "API permission" abstraction.

**Assessment.** A duplicates rules and risks drift between API and admin
("I can edit this page in the admin but not via the API"). C is what
wagtail-write-api effectively does for non-page resources; it works but
doesn't get the tree-walking behaviour for free. B reuses the exact policy
the admin already enforces, which is the right answer for pages.

**Chosen: B.** `permissions.py` exposes `can_add_subpage(user, parent)`,
`can_change(user, page)`, `can_publish(user, page)`, `can_delete(user, page)`,
each of which is a one-line wrapper around
`PagePermissionPolicy.user_has_permission_for_instance`. Endpoints call
these explicitly and raise `PermissionDenied` (caught by the API exception
handler and mapped to a 403 response).

For non-page resources (images, documents, future snippets) this is open;
the analogous wrapper hasn't been written yet because the read-only stubs
don't need permission checks beyond "can this user see this collection."
That's the natural next decision point.

### 3.11 Orderable / `InlinePanel` handling on writes

**Question.** How do nested `Orderable` rows (e.g. `BlogPage.authors`) flow
through `POST` / `PATCH`?

**Options.**
- A. Don't support nested writes; require a separate endpoint per
  Orderable model.
- B. Accept them inline in the page payload; replace the entire list on
  every write.
- C. Accept them inline with explicit `_create` / `_update` / `_delete`
  markers per row.

**Assessment.** A is the simplest but turns "create a page with three
authors" into four HTTP calls and a transaction-management problem for
clients. C is the most flexible but adds a vocabulary that doesn't match
Wagtail's admin behaviour (which also replaces the full list on save). B
matches the admin's own semantics and is what wagtail-write-api does.

**Chosen: B.** Implemented in `schemas.apply_payload` (records the
relation payloads as pending) and `save_child_payloads` (deletes existing
children and re-creates from the payload after the parent is saved). The
deletion-then-recreate approach is imperfect — it loses PKs on every
update — but matches the admin's "atomic replace" model and avoids the
"how do I express delete in JSON?" question. A future version can move to
upsert-by-id once the API has earned that complexity.

### 3.12 Status codes on success responses

**Question.** How do v3 endpoints emit non-200 success codes (e.g. 201
Created, 204 No Content)?

**Options.**
- A. Declare them via Ninja's `response={200: …, 201: …}` decorator argument.
- B. Build a `JsonResponse` / `HttpResponse` directly inside the view and
  return it.

**Assessment.** A is more idiomatic for Ninja and surfaces the status code
in OpenAPI. B is more concise and avoids re-declaring the response shape
twice (once for 200, once for 201). Given that response shapes are dicts
(decision 3.3), the OpenAPI benefit of A is mostly cosmetic.

**Chosen: B for now.** `create_page` returns `JsonResponse(payload, status=201)`;
`delete_page` returns `HttpResponse(status=204)`. When the response-schema
question (3.3) is revisited, this should be revisited too.

### 3.13 Test scope for the initial commit

**Question.** How much test coverage is appropriate for a "Phase 1–3"
commit?

**Options.**
- A. Exhaustive: every filter, every error path, every permission branch.
- B. **Smoke tests** covering the major shapes (anon read, anon write
  rejected, token write succeeds, lifecycle works).
- C. Skip tests, document manually.

**Assessment.** A is what the final v3 needs; producing it in one sitting
would slow the foundational work down disproportionately and lock in
internal API choices that are still in flux. C is irresponsible — without
tests it's impossible to demonstrate that v2 is unbroken. B gets us a
green light without over-investing in a surface that will change.

**Chosen: B.** Seven tests in `wagtail/api/v3/tests/test_pages.py` cover:
anonymous list, anonymous detail, `?type` filter, `?fields=_,title`,
anonymous write rejection, token-authenticated create, and a full
patch-publish-delete lifecycle. Additionally, all 320 v2 tests are run as
a regression check.

### 3.14 Where the v3 API gets *mounted* in the test project

**Question.** What URL prefix should v3 use on the demo site?

**Options.**
- A. `/api/v3/` (versioned, explicit).
- B. `/api/main-v3/` (mirroring v2's `/api/main/` convention).
- C. `/api/` plus content-type negotiation per `Accept` header.

**Assessment.** B is more consistent with v2 *within the test project*
but uses a meaningless string (`main`) in the URL; that's a v2 artefact
worth not replicating. C is a much bigger design decision than the
question warrants. A matches every Wagtail user's intuition for an
"API v3 endpoint."

**Chosen: A.** `path("api/v3/", v3_api.urls)` in `wagtail/test/urls.py`.

---

## 4. Known gaps and obvious next phases

Listed in roughly the order I would tackle them next:

1. **Snippets endpoints** (`/api/v3/snippets/{app_label}.{ModelName}/…`)
   — needs an analogue to `PagePermissionPolicy` for arbitrary registered
   snippet models, and a write-payload story for snippet FKs.
2. **Image and document writes** (upload + metadata + delete) — multipart
   handling, collection permissions, alt-text validation.
3. **Per-model response schemas** for accurate OpenAPI (decision 3.3).
   The cleanest path is probably to generate one schema per
   `model × {anonymous, authenticated}` pair and use Ninja's
   `response={…}` machinery; this also fixes the 201/204 hand-rolling
   from decision 3.12.
4. **Revision endpoints** (`GET /pages/{id}/revisions/`,
   `GET /pages/{id}/revisions/{rev_id}/`) — straightforward to add once
   the schema story is settled.
5. **`submit-for-moderation` and workflow integration** — Wagtail's
   workflow API is the right delegate; not yet wired.
6. **Admin UI for `ApiToken`** — currently tokens are created via the
   ORM or Django admin only; a Wagtail admin view with create/revoke
   buttons is the natural next thing for users.
7. **Documentation** under `docs/advanced_topics/api/v3/`, mirroring the
   v2 `configuration.md` / `usage.md` structure.
8. **Frontend cache integration** — v2 has `WAGTAILAPI_USE_FRONTENDCACHE`
   wiring; v3 doesn't yet.
9. **`?fields=` for write responses** — currently the response to a
   `POST` / `PATCH` ignores `?fields=` and returns the full detail view.
   Symmetry with reads would be a nicety.

---

## 5. Files touched outside `wagtail/api/v3/`

Only three files outside the new package:

- `pyproject.toml` — added `django-ninja>=1.4,<2.0` to runtime dependencies.
- `wagtail/test/settings.py` — added `wagtail.api.v3` to `INSTALLED_APPS`.
- `wagtail/test/urls.py` — added `path("api/v3/", v3_api.urls)` next to the
  existing v2 mount.

No production-code Wagtail files were modified. v2 is bit-for-bit
unchanged.
