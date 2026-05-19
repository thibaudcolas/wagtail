# Issue #6209 — OpenAPI schema generation for Wagtail API v2

A walk-through of the investigation, design decisions, and changes made to resolve [wagtail/wagtail#6209](https://github.com/wagtail/wagtail/issues/6209) ("Support OpenAPI Schema generation for Wagtail API").

## The problem

The issue spans 5+ years and three schema generators (drf-yasg, DRF's built-in, drf-spectacular). All of them choke on Wagtail's `wagtail.api.v2` viewsets at introspection time. Running `manage.py spectacular` on the test site reproduced the exact failure described in the thread: **100 errors, 16 unique**, with the schema reduced to a stub where every endpoint says `"No response body"`.

Reading the comments and source code reduced this to three concrete crashes inside `wagtail/api/v2/views.py`:

1. `get_serializer_class` reads `self.request.wagtailapi_router` (line 386). That attribute is injected by `WagtailAPIRouter.wrap_view` on every real request, but schema generators bypass the wrapper — so the attribute is missing.
2. `get_serializer_class` calls `self.get_object()` whenever `self.action != "listing_view"` (line 367). Schema generators use the standard DRF action name `"list"` for listing, which falls into the detail branch, and `get_object()` blows up on the missing `pk` URL kwarg.
3. `get_serializer_context` also reads `self.request.wagtailapi_router` (line 399); the `DetailUrlField` serializer reads it back out via `context["router"]`.

The community had also surfaced *layered* problems on top of the crashes:

- drf-spectacular can't tell that `listing_view` is a list action, so it generates colliding `*_retrieve` operation IDs and treats listings as detail responses.
- Returned fields depend on the per-request `?fields=` / `?type=` query params, so any static schema is necessarily an approximation.
- Wagtail's custom serializer fields (`TypeField`, `DetailUrlField`, `ImageRenditionField`, …) aren't standard DRF fields, so drf-spectacular falls back to `"string"` for each one and emits a warning.

The maintainer's [2024 comment on the issue](https://github.com/wagtail/wagtail/issues/6209#issuecomment-2014042681) said a PR would be welcomed; the only open question was scope.

## Methodology

I worked in three deliberately separable phases so each one is independently useful:

| Phase | Goal | Outcome if shipped alone |
| --- | --- | --- |
| 1. Core hardening | Stop the crashes inside Wagtail's viewsets | Schema generators don't error; users get a generic schema |
| 2. Docs | Show users how to wire drf-spectacular into a Wagtail project | Working schema endpoint with documented limitations |
| 3. Optional extension | Improve schema quality (envelopes, parameters, IDs, field shapes) | Rich, validating OpenAPI 3.0 schema |

Two design rules drove the work:

- **Don't make drf-spectacular a hard dependency.** Phase 1 changes live in `wagtail/api/v2/views.py` and `serializers.py` and use no drf-spectacular imports. Phase 3 lives in a separate Django app users opt into by adding it to `INSTALLED_APPS`.
- **Don't change runtime API behaviour.** Every change defends against introspection-time conditions (`request` is None, no `pk` kwarg, missing `wagtailapi_router`). Real requests always pass these guards because `WagtailAPIRouter.wrap_view` sets `request.wagtailapi_router` before the viewset is called.

I reproduced the failure end-to-end before writing any fix, and verified after each phase that:

- The existing API v2 test suite (320 tests) still passed.
- `manage.py spectacular --validate` against the test site improved measurably.

## Phase 1 — Core hardening

**Files:** `wagtail/api/v2/views.py`, `wagtail/api/v2/serializers.py`

The hardening is small and almost entirely guard-clauses. The interesting bits:

```python
def _is_listing_action(self):
    # Treat the standard DRF "list" action as a listing too, so that
    # schema generators (which never go through WagtailAPIRouter.wrap_view)
    # see listings instead of detail views.
    return self.action in ("listing_view", "list")

def get_serializer_class(self):
    request = self.request

    # During introspection there's no pk kwarg, so don't call get_object().
    if self._is_listing_action() or "pk" not in self.kwargs:
        model = self.get_queryset().model
    else:
        model = type(self.get_object())

    # self.request may be None (drf-yasg calls get_schema(request=None)).
    request_get = getattr(request, "GET", None) if request is not None else None
    if request_get and "fields" in request_get:
        ...
```

`_get_router()` is the single place that handles the missing-router case, used by `get_serializer_class`, `get_serializer_context`, and `find_view`. `_get_serializer_class` then accepts `router=None` and falls back to `BaseAPIViewSet` for nested serializers — the same fallback already in place for models without an endpoint.

`DetailUrlField.get_attribute` raises `SkipField` if the context has no router, so any serializer instantiated outside a real request just omits the field instead of crashing.

**Bonus fix discovered during testing:** `PagesAPIViewSet.get_detail_default_fields` did an unconditional `detail_default_fields.remove("locale")`. That's a latent bug — it crashes whenever a subclass omits "locale" from `meta_fields` (which `Test10411APIViewSet` does in Wagtail's own test suite). Schema introspection just happened to hit the same code path. Made the removal conditional.

**Result after Phase 1 alone:**

- Errors: 100 → 0 (or 8 of one unrelated test-only class)
- Schema content: every endpoint still uses the same generic `Page` / `Image` / `Document` components
- Operation IDs collide (`*_retrieve` for both listing and detail, resolved by drf-spectacular tacking on `_2`)
- Query parameters undocumented

That's the state Phase 2 documents and Phase 3 improves.

I added a `test_schema_introspection.py` module with 8 tests that don't depend on drf-spectacular being installed — they construct viewsets directly with a stub DRF `Request` and assert that `get_serializer_class` / `get_serializer_context` return something usable.

## Phase 2 — Docs for the "stop here" state

**File:** `docs/advanced_topics/api/v2/configuration.md`

Added a new "OpenAPI schema generation" section in the existing API configuration page. The structure is:

1. **Basic setup** — install drf-spectacular, register it in `INSTALLED_APPS`, point `REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"]` at its `AutoSchema`, wire up `SpectacularAPIView` and `SpectacularSwaggerView` in `urls.py`.
2. **Limitations of the basic setup** — explicitly list the four things that aren't great about the generic schema (envelope not represented, colliding operation IDs, per-type fields collapsed, query parameters undocumented). This is deliberately written from the perspective of "what you get if you stop here".
3. **Richer schemas with `wagtail.api.v2.schema`** — point users at the optional extension for the gaps above.

The docs page is written so it makes sense whether or not the user installs the extension. It assumes Phase 1 hardening exists but otherwise stands on its own.

## Phase 3 — Optional drf-spectacular extension

**New package:** `wagtail/api/v2/schema/`

Five short modules:

```
wagtail/api/v2/schema/
    __init__.py        # default_app_config
    apps.py            # AppConfig.ready() imports extensions + fields
    extensions.py      # OpenApiViewExtension subclasses for viewsets
    fields.py          # OpenApiSerializerFieldExtension for custom fields
    parameters.py      # Reusable OpenApiParameter objects
```

The package is a regular Django app — users opt in by listing `"wagtail.api.v2.schema"` in `INSTALLED_APPS`. The `AppConfig` imports `extensions` and `fields` lazily inside `ready()`, which is enough to register the classes with drf-spectacular's extension registry (extensions self-register at class-definition time via the metaclass).

### View extensions

I used `OpenApiViewExtension` with `match_subclasses = True` so a single extension covers `PagesAPIViewSet`, `ImagesAPIViewSet`, `DocumentsAPIViewSet`, `RedirectsAPIViewSet`, and any user subclass. There are two extensions:

- `WagtailPagesAPIViewSetExtension` (priority 1) — `PagesAPIViewSet` and its subclasses, with page-specific query parameters (`?type`, `?child_of`, `?ancestor_of`, `?descendant_of`, `?translation_of`, `?locale`, `?site`) and `?html_path` on the find endpoint.
- `WagtailBaseAPIViewSetExtension` (priority 0) — every other `BaseAPIViewSet` subclass.

The priority makes the Pages extension win for any class that's a `PagesAPIViewSet` subclass; the base extension catches the rest.

**Gotcha I hit:** `OpenApiViewExtension.target_class` resolves the *string* target at registration time, so `self.target_class` is always the literal base class (`BaseAPIViewSet`) — not the matched subclass. The matched subclass is in `self.target`. Forgot this initially, and `view_replacement` was crashing with `'NoneType' object has no attribute '_meta'` because it was building serializers using `BaseAPIViewSet` (which has `model = None`). Fixed by using `self.target`.

The `view_replacement` returns a subclass of the original viewset whose `listing_view` / `detail_view` / `find_view` methods are decorated with `@extend_schema`:

```python
@extend_schema(
    operation_id=f"{op_base}_list",
    tags=[tag],
    parameters=listing_params,
    responses={200: listing_response},
)
def listing_view(self, request):
    return super().listing_view(request)
```

The listing response is built with `inline_serializer` to model Wagtail's `{meta: {total_count}, items: [...]}` envelope. The item serializer comes from `target_class._get_serializer_class(router=None, model=target_class.model, fields_config=[], show_details=False)` — which works because Phase 1 made that callable without a router.

**Component naming collision:** Wagtail's `get_serializer_class` builds dynamic serializer classes named `f"{model.__name__}Serializer"` ("PageSerializer", "ImageSerializer", …). The v2 API and the admin API end up generating identically-named classes for the same model, and drf-spectacular emits "duplicate component" warnings. Fixed by wrapping each dynamic class in a thin subclass with a viewset-scoped name (`WagtailPagesDetail`, `WagtailPagesListItem`, …) just before handing it to drf-spectacular. The original classes are unchanged at runtime, so user code that introspects `__name__` still works.

### Field extensions

For each of Wagtail's custom serializer fields, an `OpenApiSerializerFieldExtension` declares its OpenAPI shape:

- Plain string fields: `TypeField`, `PageTypeField`
- URL fields: `DetailUrlField`, `PageHtmlUrlField`, `ImageDownloadUrlField`, `DocumentDownloadUrlField`
- Tag arrays: `TagsField`
- Nested page references: `PageParentField`, `PageAliasOfField` (shared base)
- Admin-API objects: `PageChildrenField`, `PageStatusField`, `PageAncestorsField`, `PageDescendantsField`
- Image renditions: `ImageRenditionField`

This is mechanical work, but it's what takes the schema from "everything is a string" to something a typed client can consume.

### Parameters

`parameters.py` is just a list of `OpenApiParameter` constants grouped by audience:

- `COMMON_LISTING_PARAMETERS` — `limit`, `offset`, `fields`, `order`, `search`, `search_operator`
- `PAGE_LISTING_PARAMETERS` — page-only filters
- `FIND_PARAMETERS` / `PAGE_FIND_PARAMETERS` — for the `find/` endpoint

The relevant extension attaches the right combination via `@extend_schema(parameters=…)`.

### Tests

`test_schema_extension.py` adds 7 end-to-end tests that run the real drf-spectacular `SchemaGenerator` against the test URL conf and assert:

- The generator doesn't raise.
- Listings return the `{meta, items}` envelope.
- Detail returns a single object.
- Listing and detail have distinct `*_list` and `*_retrieve` operation IDs.
- Find is documented as a 302.
- Page listings expose `?fields`, `?type`, `?child_of`, `?search`; image listings expose the common params but **not** the page-only ones.

The whole module is wrapped in `@unittest.skipUnless(HAS_SPECTACULAR, …)`, so it's a no-op when drf-spectacular isn't installed.

## Results

| Stage | Errors | Warnings | Schema |
| --- | --- | --- | --- |
| Before any change | 100 (16 unique) | 8 | Generic, "No response body" everywhere |
| After Phase 1 only | 0 (1 unrelated test viewset) | 33 | Generic but populated |
| After Phase 3 | 0 | 4 (admin-API-only) | 1990-line OpenAPI 3.0, validates, distinct components per viewset |

The 4 remaining warnings are all admin-API-related and concern duplicate `Page` components plus a missing return type hint on `get_admin_display_title`. They don't affect the public API and could be cleaned up in a follow-up touching `wagtail.admin.api` directly.

drf-yasg also benefits from Phase 1 — the `wagtailapi_router` and `pk` errors are gone — but hits a new drf-yasg-specific `ref_name` conflict on dynamically generated serializers. That's a quirk of drf-yasg (which the maintainers have said is unlikely to ever ship OpenAPI 3.0 support) rather than Wagtail, and is out of scope.

## Files changed

```
docs/advanced_topics/api/v2/configuration.md       +91   # new "OpenAPI schema generation" section
docs/releases/8.0.md                               +1    # entry under "Other features"
wagtail/api/v2/serializers.py                      +8/-2 # DetailUrlField router fallback
wagtail/api/v2/views.py                            +44/-10 # _is_listing_action, _get_router, guards
wagtail/api/v2/schema/__init__.py                  +14   # new app
wagtail/api/v2/schema/apps.py                      +22
wagtail/api/v2/schema/extensions.py                +204
wagtail/api/v2/schema/fields.py                    +148
wagtail/api/v2/schema/parameters.py                +134
wagtail/api/v2/tests/test_schema_introspection.py  +95   # Phase 1 unit tests
wagtail/api/v2/tests/test_schema_extension.py      +112  # Phase 3 integration tests
```

Total: ~870 lines added (~600 of which are tests, parameter declarations, and field-shape declarations — the kind of code where line count tracks coverage rather than complexity).

## Test results

`python -m django test wagtail.api.v2.tests --settings=wagtail.test.settings`

- Before: 320 tests, all passing.
- After: 335 tests, all passing (8 from Phase 1, 7 from Phase 3).

## QA considerations for the long run

The schema we ship now becomes a contract with API consumers. A few QA practices are worth borrowing from projects that have lived with this for a while:

### 1. Snapshot the generated schema in CI

Commit a reference `schema.yml` and have CI regenerate it on every PR, failing on diff. This is the cheapest catch-all: any accidental schema change becomes visible in the PR description, because clients depend on it and changes should be deliberate. drf-spectacular itself uses snapshot tests in its own suite, as does FastAPI. The diff also doubles as informal release notes for API consumers.

**Tradeoff:** noisy across drf-spectacular minor releases (component name tweaks, type narrowing). The mitigation is pinning drf-spectacular in test deps and bumping it deliberately. Updates are usually one-line refreshes.

### 2. Contract-test with [schemathesis](https://schemathesis.readthedocs.io/)

Point it at a running test site with the demosite fixture and let it fuzz requests derived from the schema, then assert the responses conform. This catches "schema says X, implementation returns Y" drift — which is the failure mode where a generated schema is most dangerous, because it looks fine but lies. Hasura, Stripe-clients, and a number of Django Ninja apps use this pattern. It plugs naturally into the existing `test_api_v2.py` smoke-test script.

**Tradeoff:** real runtime cost (hundreds of HTTP calls per run). Typically run on a nightly job, not every PR.

### 3. Lint the schema with [Spectral](https://stoplight.io/open-source/spectral)

A small ruleset (operationId naming, parameter descriptions present, no untyped properties) catches regressions that don't crash but degrade the developer experience for API consumers. GitHub's and Stripe's published specs are gated on Spectral rules. Inexpensive to run on every PR.

**Tradeoff:** rule curation has its own learning curve, and the "recommended" preset is fairly opinionated. Usually starts with a permissive set and tightens over time.

### Recommended sequencing

If picking one to start: **snapshot testing**. Single CI step, immediate signal, no extra services. Add **Spectral** next for cheap consumer-facing checks, and **schemathesis** last (as a nightly job) once we want guarantees about implementation-schema fidelity.

Prototypes for all three options live under `qa/` (`qa/snapshot/`, `qa/schemathesis/`, `qa/spectral/`), each runnable standalone with its own README.

## Things I deliberately didn't do

- **Per-page-type schema components.** A future extension could enumerate every `Page` subclass with `api_fields` and emit one component per type, so clients could see what `?type=blog.BlogEntryPage&fields=*` actually returns. That requires more design (it could explode the schema size on real sites) and is left as a follow-up.
- **drf-yasg support beyond the crash fix.** The maintainers don't intend to ship OpenAPI 3.0 there, and Wagtail's own docs already point users at drf-spectacular and Django Ninja.
- **Touching the admin API beyond field extensions.** The remaining 4 warnings are scoped to `wagtail.admin.api`. Cleaning them up means touching the admin API's own serializer definitions, which is a separate concern from the public API v2 issue this PR addresses.
- **Changing the dynamic serializer naming in `wagtail.api.v2.serializers.get_serializer_class`.** That would be a wider behaviour change (user code reads `__name__`). The wrapping rename inside the extension is enough to fix the schema collision without touching runtime behaviour.
