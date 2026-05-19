(api_v3_usage)=

# Wagtail API v3 usage guide

The v3 Wagtail API module exposes a JSON-formatted **read/write** API
which can be used by external clients (such as a mobile app), the site's
frontend, or programmatic integrations that need to create and update
content.

This document is intended for developers calling the API exposed by
Wagtail. For documentation on how to enable the API module in your
Wagtail site, see [Wagtail API v3 configuration guide](/advanced_topics/api/v3/configuration).

Contents

```{contents}
---
local:
depth: 3
---
```

## Fetching content

To fetch content over the API, perform a `GET` request against one of
the following endpoints:

- Pages: `/api/v3/pages/`
- Images: `/api/v3/images/`
- Documents: `/api/v3/documents/`

```{note}
The available endpoints and their URLs may vary from site to site,
depending on how the API has been configured.
```

### Example response

Each response contains the list of items (`items`) and the total count
(`meta.total_count`). The total count is irrespective of pagination.
This envelope is identical to v2.

```text
GET /api/v3/endpoint_name/

HTTP 200 OK
Content-Type: application/json

{
    "meta": {
        "total_count": "total number of results"
    },
    "items": [
        {
            "id": 1,
            "meta": {
                "type": "app_name.ModelName",
                "detail_url": "https://api.example.com/api/v3/endpoint_name/1/"
            },
            "field": "value"
        },
        {
            "id": 2,
            "meta": {
                "type": "app_name.ModelName",
                "detail_url": "https://api.example.com/api/v3/endpoint_name/2/"
            },
            "field": "different value"
        }
    ]
}
```

(api_v3_custom_page_fields)=

### Custom page fields in the API

Wagtail sites contain many page types, each with their own set of
fields. The `pages` endpoint will only expose the common fields by
default (such as `title` and `slug`).

For anonymous callers, the `?type` parameter selects a specific page
model and unlocks the fields it has listed in `api_fields`:

```
GET /api/v3/pages/?type=blog.BlogPage&fields=published_date,body,authors(name)

HTTP 200 OK
Content-Type: application/json

{
    "meta": {
        "total_count": 10
    },
    "items": [
        {
            "id": 1,
            "meta": {
                "type": "blog.BlogPage",
                "detail_url": "https://api.example.com/api/v3/pages/1/",
                "html_url": "https://www.example.com/blog/my-blog-post/",
                "slug": "my-blog-post"
            },
            "title": "Test blog post",
            "published_date": "2016-08-30",
            "authors": [
                {
                    "id": 1,
                    "meta": {
                        "type": "blog.BlogPageAuthor"
                    },
                    "name": "Karl Hobley"
                }
            ]
        },

        ...
    ]
}
```

```{note}
For **anonymous** callers, only fields explicitly exported via
`api_fields` are accessible. For **authenticated** callers (session
cookie or bearer token), all editable fields on the page model are
accessible without further declaration. See
[](api_v3_field_visibility) in the configuration guide for the full
rules and trade-offs.
```

This doesn't apply to images/documents as there is only one model
exposed in those endpoints. But for projects that have customized
image/document models, the `api_fields` attribute can be used to export
any custom fields into the API for anonymous callers.

(api_v3_pagination)=

### Pagination

The number of items in the response can be changed by using the
`?limit` parameter (default: 20) and the number of items to skip can be
changed by using the `?offset` parameter.

```
GET /api/v3/pages/?offset=20&limit=20

HTTP 200 OK
Content-Type: application/json

{
    "meta": {
        "total_count": 50
    },
    "items": [
        pages 20 - 40 will be listed here.
    ]
}
```

```{note}
There may be a maximum value for the `?limit` parameter. This can be
modified in your project settings by setting `WAGTAILAPI_LIMIT_MAX` to
either a number (the new maximum value) or `None` (which disables the
maximum value check).
```

(api_v3_usage_ordering)=

### Ordering

The results can be ordered by any field by setting the `?order`
parameter to the name of the field to order by.

```
GET /api/v3/pages/?order=title
```

The results will be ordered in ascending order by default. This can be
changed to descending order by prefixing the field name with a `-`
sign.

```
GET /api/v3/pages/?order=-title
```

#### Multiple ordering

Multiple fields can be passed into `?order` for consecutive ordering.

```
GET /api/v3/pages/?order=title,-slug
```

#### Random ordering

Passing `random` into the `?order` parameter will return the results in
a random order.

```
GET /api/v3/pages/?order=random
```

```{note}
It's not possible to use `?offset` while ordering randomly because
consistent random ordering cannot be guaranteed over multiple requests.
```

### Filtering

Any field on the resolved page model may be used in an exact match
filter. Use the field name as the query-parameter name and the value to
match against.

For example, to find a page with the slug "about":

```
GET /api/v3/pages/?slug=about
```

(api_v3_filter_by_tree_position)=

### Filtering by tree position (pages only)

Pages can additionally be filtered by their relation to other pages in
the tree.

The `?child_of` filter takes the id of a page and filters the list of
results to contain only the direct children of that page.

```
GET /api/v3/pages/?child_of=2&show_in_menus=true
```

The `?ancestor_of` filter takes the id of a page and filters the list
to only include ancestors of that page (parent, grandparent etc.) all
the way down to the site's root page.

The `?descendant_of` filter takes the id of a page and filters the
list to only include descendants of that page (children, grandchildren,
etc.).

(api_v3_filtering_pages_by_site)=

### Filtering pages by site

By default, the API will look for the site based on the hostname of the
request. The `?site=` filter is used to filter the listing to only
include pages that belong to a specific site. The filter requires the
configured hostname; for multiple sites sharing the same hostname,
include a port using `hostname:port`:

```
GET /api/v3/pages/?site=demo-site.local
GET /api/v3/pages/?site=demo-site.local:8080
```

### Search

Passing a query to the `?search` parameter will perform a full-text
search using Wagtail's search backend.

The `?search_operator` parameter accepts `and` or `or` and is honoured
when the backend supports it. The defaults match v2.

```
GET /api/v3/pages/?search=James+Joyce&order=-first_published_at&search_operator=and
```

```{note}
Ordering is applied **before** search to keep the query plan compatible
with backends (such as the database backend) whose `SearchResults`
cannot be further re-ordered. This is the same trade-off as v2.
```

(api_v3_i18n_filters)=

### Special filters for internationalized sites

When `WAGTAIL_I18N_ENABLED` is set to `True` (see
[](enabling_internationalisation)), two extra filters are accepted on
the pages endpoint.

#### Filtering pages by locale

The `?locale=` filter restricts the listing to pages in the specified
locale:

```
GET /api/v3/pages/?locale=en-us
```

#### Getting translations of a page

The `?translation_of` filter restricts the listing to pages that are a
translation of the supplied page id:

```
GET /api/v3/pages/?translation_of=10
```

(api_v3_fields)=

### Fields

By default, only a subset of the available fields are returned in the
response. The `?fields` parameter can be used to both add additional
fields to the response and remove default fields you do not need.

#### Additional fields

Additional fields can be added by setting `?fields` to a comma-separated
list of field names:

```
?fields=body,feed_image
```

Sub-fields are supported across relationships:

```
?fields=body,feed_image(width,height)
```

#### All fields

Setting `?fields` to an asterisk (`*`) will add all available fields to
the response:

```
?fields=*
```

For anonymous callers this means all fields declared in `api_fields`.
For authenticated callers it means all editable fields on the page
model.

#### Removing fields

Fields can be removed by prefixing the name with `-`:

```
?fields=-title,body
```

This also combines with the asterisk:

```
?fields=*,-body
```

#### Removing all default fields

The leading `_` clears the default field set so the rest of the list
specifies exactly which fields to return:

```
?fields=_,title
```

### Detail views

Append an object's id to the URL to retrieve a single object:

- Pages: `/api/v3/pages/1/`
- Images: `/api/v3/images/1/`
- Documents: `/api/v3/documents/1/`

All exported fields are returned by default. The `?fields` parameter
customises which fields are returned:

```
/api/v3/pages/1/?fields=_,title,body
```

(api_v3_finding_pages_by_path)=

### Finding pages by HTML path

You can find an individual page by its HTML path using
`/api/v3/pages/find/?html_path=<path>`. The endpoint returns either a
`302` redirect to the page's detail view, or a `404`.

For example, `/api/v3/pages/find/?html_path=/` always redirects to the
homepage of the site.

(api_v3_writes)=

## Writing content

All write endpoints require authentication. The simplest way to call
them from a script is with a bearer token:

```sh
curl -X POST https://api.example.com/api/v3/pages/ \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"type": "blog.BlogPage", "parent": 3, "title": "Hello world"}'
```

Browser-based callers using a Wagtail admin session must also include
the CSRF token in the `X-CSRFToken` header, the same as any
session-authenticated unsafe request. See [authentication](api_v3_configuration)
in the configuration guide.

### Creating a page

```
POST /api/v3/pages/

{
    "type": "blog.BlogPage",
    "parent": 3,
    "title": "Hello world",
    "slug": "hello-world",      // optional; derived from title if omitted
    "body": [...],              // optional; model-specific fields
    "authors": [                // optional; nested Orderable rows
        {"name": "Karl"}
    ]
}
```

- `type` is required and must be in `app_label.ModelName` form. It must
  refer to a concrete subclass of `Page`.
- `parent` is required and must be the id of an existing page the
  authenticated user has `add` permission on.
- Any other key is matched against the page model's editable fields:
  unknown keys are returned in the response under `meta.skipped_fields`
  rather than rejecting the whole request.
- Pages are always created as drafts (`live: false`). Publish them
  separately via [`/publish/`](api_v3_publish_unpublish) or include
  publishing as a follow-up step in your client.

A successful create returns `201` with the full detail view of the new
page.

### Updating a page

```
PATCH /api/v3/pages/{id}/

{
    "title": "A new title",
    "authors": [{"name": "Karl"}, {"name": "Matt"}]
}
```

- A new revision is saved on every successful `PATCH`. The
  `meta.latest_revision` field on the response is the id of that new
  revision.
- Nested Orderable lists (such as `authors` above) are **replaced**
  wholesale; the previous rows are deleted and re-created in the order
  given. This matches Wagtail admin behaviour.
- Unknown keys are reported under `meta.skipped_fields`.
- Patching does not publish the page; call `/publish/` separately.

Rich text fields are accepted in Wagtail's storage format (the raw
value, including `<embed>` and `<a linktype="…">` tags). The same
format is returned by reads, so values round-trip.

### Deleting a page

```
DELETE /api/v3/pages/{id}/
```

Returns `204 No Content` on success.

(api_v3_publish_unpublish)=

### Publishing and unpublishing

```
POST /api/v3/pages/{id}/publish/
POST /api/v3/pages/{id}/unpublish/
```

Both endpoints take no request body. `publish` saves a fresh revision
of the current draft state and publishes it; `unpublish` transitions
the page to `live: false`. Both require the `publish` permission on the
target page.

### Copying

```
POST /api/v3/pages/{id}/copy/

{
    "destination": 7,                       // optional; defaults to same parent
    "recursive": false,                     // optional
    "update_attrs": {"slug": "new-slug"}    // optional
}
```

- The new page is created as a draft, regardless of the source's live
  state.
- The caller must have `add` permission on the destination parent.

Returns `201` with the new page's detail view.

### Moving

```
POST /api/v3/pages/{id}/move/

{
    "destination": 7,           // required
    "position": "last-child"    // optional; one of "first-child", "last-child",
                                //   "left", "right" — defaults to "last-child"
}
```

The caller must have `change` permission on the page **and** `add`
permission on the destination parent.

## Default endpoint fields

### Common fields

These fields are returned by every endpoint.

**`id` (number)**
The unique ID of the object.

```{note}
Except for page types, every other content type has its own ID space,
so you must combine this with the `meta.type` field to get a unique
identifier for an object.
```

**`meta.type` (string)**
The type of the object in `app_label.ModelName` format.

**`meta.detail_url` (string)**
The absolute URL of the detail view for the object.

### Pages

**`title` (string)**
**`meta.slug` (string)**
**`meta.show_in_menus` (boolean)**
**`meta.seo_title` (string)**
**`meta.search_description` (string)**
**`meta.first_published_at` (date/time)**
**`meta.locale` (string)** (when `WAGTAIL_I18N_ENABLED` is `True`)

**`meta.html_url` (string)**
If the site has an HTML frontend, this is the URL where the page is
served.

### Images

**`title` (string)**
The image's title (used as the `alt` attribute by Wagtail itself).

**`width` (number)** and **`height` (number)**
The size of the original image file.

### Documents

**`title` (string)**

(api_v3_schema)=

## OpenAPI schema

The API auto-generates an OpenAPI 3.1 document at
`/api/v3/openapi.json` and serves a Swagger UI at `/api/v3/docs`.

```{note}
The OpenAPI document advertises read responses as generic `object`
shapes rather than per-model schemas, because the response shape varies
with the caller's auth state and `?fields=` parameter. Per-model
schemas are planned for a future release; until then, treat the
OpenAPI document as a catalogue of endpoints and parameters rather than
an exhaustive type definition.
```

## Differences from v2

### Behavioural differences

- **Writes.** v3 supports `POST`, `PATCH`, and `DELETE` on the pages
  endpoint, plus action endpoints (`publish`, `unpublish`, `copy`,
  `move`). v2 is read-only.
- **Authentication.** v3 ships with a bearer-token model
  (`wagtail.api.v3.models.ApiToken`) and accepts the Django admin
  session cookie. v2 has no built-in token system.
- **Field visibility per caller.** Anonymous callers continue to see
  only fields listed in `api_fields`; authenticated callers see every
  editable field. See [](api_v3_field_visibility) for the rationale.
- **Default listing fields.** Listings include `id`, `type`,
  `detail_url`, `title`, `html_url`, `slug`. `first_published_at` is
  available via `?fields=first_published_at` but is not in the default
  set; v2 includes it in the listing default.

### Structural differences

- **Underlying framework.** v3 is built on Django Ninja; v2 is built on
  Django REST Framework. Subclassing v2 viewsets does not work for v3.
- **Custom endpoint registration.** v3 uses
  `api.add_router("/foo/", router)`. v2 uses
  `WagtailAPIRouter.register_endpoint(name, ViewSet)`.
- **Pagination envelope.** Identical to v2:
  `{"meta": {"total_count": N}, "items": [...]}`.
- **Schema introspection.** v3 ships `/api/v3/openapi.json` and a
  Swagger UI at `/api/v3/docs`. v2 has no equivalent.

### Features not yet ported

The following v2 features are not (yet) available in v3:

- Custom `APIField` serializers — the `serializer=` argument is
  accepted by the model attribute but ignored by v3.
- `ImageRenditionField` for inline rendition URLs.
- Frontend cache invalidation.
- Image and document write operations.
- Snippets endpoint.

If any of these matter for your use case, continue using v2 alongside
v3 (both can be enabled in the same project) until the gap is closed.
