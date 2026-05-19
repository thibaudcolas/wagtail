(api_v3_configuration)=

# Wagtail API v3 configuration guide

This section of the docs will show you how to set up a public **read/write**
API for your Wagtail site using `wagtail.api.v3`.

The v3 API is built on [Django Ninja](https://django-ninja.dev/), which
ships with `wagtail` as a runtime dependency. It runs alongside the older
[v2 API](api_v2_configuration); both can be enabled in the same project.

```{note}
Unlike v2, the v3 API supports **writes** (creating, updating, publishing,
moving, copying, and deleting pages) in addition to reads. Anonymous
callers can still read public content; all write operations require an
authenticated request (session cookie or bearer token).
```

(api_v3_scope)=

## Status and scope

v3 is being introduced incrementally. The current release covers:

- Pages: full CRUD (`GET`, `POST`, `PATCH`, `DELETE`) plus `publish`,
  `unpublish`, `copy`, `move`, and `find` actions.
- Images and documents: read-only listings and detail views.
- Bearer-token authentication (`wagtail.api.v3.models.ApiToken`) and
  Django session authentication.
- Auto-generated OpenAPI document at `/<prefix>/openapi.json` and a
  Swagger UI at `/<prefix>/docs`.

Not yet included (deferred to subsequent releases):

- Image and document write operations.
- A snippets endpoint.
- Per-model OpenAPI response schemas (the response shape is dynamic — see
  the section on [field visibility](api_v3_field_visibility) — so the
  OpenAPI document currently advertises `object` rather than per-model
  shapes).
- Custom serializers attached to individual `APIField` entries (the v2
  pattern `APIField("foo", serializer=...)` is read but not yet honoured).
- Frontend-cache invalidation hooks.

## Basic configuration

### Enable the app

Add `wagtail.api.v3` to `INSTALLED_APPS` in your Django project settings:

```python
# settings.py

INSTALLED_APPS = [
    ...

    'wagtail.api.v3',

    ...
]
```

Run migrations so the bearer-token table is created:

```sh
./manage.py migrate
```

(api_v3_configure_endpoints)=

### Configure endpoints

Each content type (pages, images, documents) is exposed by its own Django
Ninja router. The convenience factory `wagtail.api.v3.api.build_api()`
returns a configured `NinjaAPI` instance with all three routers
pre-registered:

```python
# api.py

from wagtail.api.v3.api import build_api

api = build_api()
```

You can then mount the router into your project's URL configuration:

```python
# urls.py

from django.urls import path
from .api import api

urlpatterns = [
    ...

    path('api/v3/', api.urls),

    ...

    # Ensure that the api line appears above the default Wagtail page
    # serving route
    re_path(r'^', include(wagtail_urls)),
]
```

With this configuration, the endpoints will be available at:

- Pages: `/api/v3/pages/`
- Images: `/api/v3/images/`
- Documents: `/api/v3/documents/`
- OpenAPI document: `/api/v3/openapi.json`
- Swagger UI: `/api/v3/docs`

### Adding your own endpoints

Django Ninja composes routers rather than registering view classes. To
add a custom endpoint, build a `Router` and attach it to the API instance
returned by `build_api()`:

```python
# api.py

from ninja import Router
from wagtail.api.v3.api import build_api

posts_router = Router()


@posts_router.get("")
def list_posts(request):
    return {"items": [...], "meta": {"total_count": 0}}


api = build_api()
api.add_router("/posts/", posts_router)
```

```{note}
v3 does **not** use v2's `WagtailAPIRouter.register_endpoint(name, ViewSet)`
pattern. Custom endpoints are plain Django Ninja routers; subclassing the
built-in pages/images/documents routers is not supported.
```

(api_v3_page_fields_configuration)=

### Adding custom page fields

The `api_fields` attribute on Wagtail page models continues to control
which custom fields are exposed to **anonymous** read callers. This is
identical to the [v2 declaration syntax](apiv2_page_fields_configuration):

```python
# blog/models.py

from wagtail.api import APIField

class BlogPageAuthor(Orderable):
    page = models.ForeignKey('blog.BlogPage', on_delete=models.CASCADE, related_name='authors')
    name = models.CharField(max_length=255)

    api_fields = [
        APIField('name'),
    ]


class BlogPage(Page):
    published_date = models.DateTimeField()
    body = RichTextField()
    feed_image = models.ForeignKey('wagtailimages.Image', on_delete=models.SET_NULL, null=True, ...)
    private_field = models.CharField(max_length=255)

    api_fields = [
        APIField('published_date'),
        APIField('body'),
        APIField('feed_image'),
        APIField('authors'),
    ]
```

(api_v3_field_visibility)=

#### How field visibility differs from v2

In v2 the `api_fields` whitelist applies to every caller. In v3 the rule
depends on whether the request is authenticated:

| Caller        | Fields visible by default                                  |
| ------------- | ---------------------------------------------------------- |
| Anonymous     | Common page fields + each model's declared `api_fields`    |
| Authenticated | Common page fields + every editable field on the model     |

Authenticated callers (session cookie or bearer token) therefore see all
custom fields without needing them to be listed in `api_fields`. This is
the same dataset the admin form would expose. A small set of internal
fields (`path`, `depth`, `numchild`, `live_revision`, `latest_revision`,
…) is never exposed, regardless of auth state.

The `?fields=` query parameter still works exactly as in v2 (see
[](api_v3_fields)).

```{warning}
If you rely on `api_fields` as a security boundary — i.e., to hide some
model fields from clients — make sure those clients are not authenticating
as a user with admin permissions. With v3, an authenticated request sees
every editable field unless the field is in the internal protected list.
```

### Rich text in the API

v3 returns rich-text fields in Wagtail's storage format (the raw value
read from the database, with `<embed>`/`<a linktype=...>` tags). This is
the same format described in [](../../../extending/rich_text_internals).

Write operations accept the same format on input — what you read out can
be written back. Format negotiation (Markdown ↔ display HTML ↔ Wagtail
internal) is deferred to a future release.

### Authentication

v3 supports two authentication mechanisms, both wired into write
endpoints by default:

1. **Django session cookie**, as used by the admin UI. Browser-based
   tools that are already logged into Wagtail admin can call v3
   endpoints with no further setup, as long as they send a CSRF token
   (`X-CSRFToken` header) on unsafe methods.

2. **Bearer token**, suitable for headless / scripted clients. A token
   is a row in `wagtail.api.v3.models.ApiToken` and presented as:

   ```
   Authorization: Bearer <key>
   ```

   No CSRF is required when using bearer auth.

#### Creating tokens

Tokens are tied to a Django user; permission decisions still flow
through Wagtail's `PagePermissionPolicy`, so a token never grants more
access than the underlying user already has.

```python
# in a shell or management command
from django.contrib.auth import get_user_model
from wagtail.api.v3.models import ApiToken

user = get_user_model().objects.get(username="alice")
token = ApiToken.objects.create(user=user, label="CI runner")
print(token.key)  # 32-byte URL-safe random key
```

Tokens can also be revoked (`token.revoked = True; token.save()`) or
deleted. `last_used` is updated each time a token successfully
authenticates a request.

```{note}
If you use bearer-token authentication in production you must ensure that
your API is only available over `https`. The token key is sent as plain
text in the `Authorization` header.
```

```{note}
A Wagtail admin UI for managing tokens is not yet provided. Tokens are
created via the Django admin or programmatically as shown above.
```

#### Permissions

All write endpoints check the authenticated user against Wagtail's
existing `PagePermissionPolicy`. The relevant actions are:

| Endpoint                             | Permission action required                |
| ------------------------------------ | ----------------------------------------- |
| `POST /pages/`                       | `add` on the target parent page           |
| `PATCH /pages/{id}/`                 | `change` on the target page               |
| `DELETE /pages/{id}/`                | `delete` on the target page               |
| `POST /pages/{id}/publish/`          | `publish` on the target page              |
| `POST /pages/{id}/unpublish/`        | `publish` on the target page              |
| `POST /pages/{id}/copy/`             | `add` on the destination parent           |
| `POST /pages/{id}/move/`             | `change` on the page + `add` on dest.     |

Anonymous callers receive `401` or `403` on any write endpoint without
hitting the underlying logic.

## Additional settings

### `WAGTAILAPI_LIMIT_MAX`

(default: 20)

Maximum value accepted for the `?limit` query parameter. Set to `None`
for no limit. This setting is shared with v2.

### `WAGTAILAPI_BASE_URL`

(used by `wagtail.contrib.frontend_cache` when invalidating cached
responses)

v3 does not yet integrate with the frontend cache, but this setting
remains valid for v2 in the same project.
