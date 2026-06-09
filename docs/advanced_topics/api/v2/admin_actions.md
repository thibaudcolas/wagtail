(api_v2_admin_actions)=

# Page write actions (admin API)

The public Wagtail API v2 documented in [](api_v2_usage) is read-only. For projects that need to mutate pages through HTTP — moving them around the tree, publishing, copying, locking, etc. — Wagtail exposes a separate, session-authenticated endpoint on the admin API:

```
POST /admin/api/main/pages/<id>/action/<action_name>/
```

```{warning}
The admin API is intentionally undocumented as a public stability surface. Action names and payloads have been stable in practice for several major releases, but they are not part of Wagtail's stability policy in the same way as the v2 public endpoints. If you build a third-party integration on top of these endpoints, pin a Wagtail version range and re-test on upgrades.
```

## Authentication

The admin API uses Django REST framework's [`SessionAuthentication`](https://www.django-rest-framework.org/api-guide/authentication/#sessionauthentication), so callers must:

1. Sign in via `/admin/login/` (any superuser or a user with the relevant `wagtailadmin.access_admin` permission).
2. Send the resulting `sessionid` cookie on every request.
3. For state-changing requests (everything under `/action/`, which is `POST`), include the `csrftoken` cookie value in an `X-CSRFToken` header and a `Referer` that matches the host.

See [](api_v2_authentication) for alternatives like token or OAuth auth — these apply to the **public** v2 API only by default; using them on the admin API requires subclassing `PagesAdminAPIViewSet` and overriding its `authentication_classes`.

## Built-in actions

All built-in actions accept JSON request bodies. Successful responses return the serialized page via `AdminPageSerializer` (the same shape as `GET /admin/api/main/pages/<id>/`), except `delete` which returns `204 No Content`.

| Action | Request body | Success status | Notes |
|---|---|---|---|
| `publish` | _(none)_ | `200` | Idempotent. Creates a fresh revision if no draft exists. |
| `unpublish` | `recursive: bool=false` | `200` | `recursive=true` unpublishes the entire subtree. |
| `copy` | `destination_page_id?`, `recursive=false`, `keep_live=true`, `slug?`, `title?` | `201` | Defaults to copying to the same parent. Auto-resolves slug collisions if `slug` is omitted. |
| `move` | `destination_page_id` (required), `position?` | `200` | `position` is one of `left`, `right`, `first-child`, `last-child`, `first-sibling`, `last-sibling`. |
| `delete` | _(none)_ | `204` | Deletes the page and all descendants. |
| `create_alias` | `destination_page_id?`, `recursive=false`, `update_slug?` | `201` | Returns the new alias page. |
| `convert_alias` | _(none)_ | `200` | The page must already be an alias. |
| `copy_for_translation` | `locale` (e.g. `"fr"`), `copy_parents=false`, `alias=false`, `recursive=false` | `201` | Requires `WAGTAIL_I18N_ENABLED=True`. |
| `revert_to_page_revision` | `revision_id` (required) | `200` | The revision must belong to the page. |
| `lock` | _(none)_ | `200` | Idempotent. Sets `locked`, `locked_by`, `locked_at`. |
| `unlock` | _(none)_ | `200` | Idempotent. Clears the lock fields. |

### Example: publish a page

```bash
curl -X POST https://example.com/admin/api/main/pages/12/action/publish/ \
  -b cookies.txt \
  -H "X-CSRFToken: <csrftoken-cookie-value>" \
  -H "Referer: https://example.com/admin/api/main/pages/12/action/publish/" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Example: move a page under a different parent

```http
POST /admin/api/main/pages/42/action/move/ HTTP/1.1
Content-Type: application/json

{
  "destination_page_id": 7,
  "position": "last-child"
}
```

## Error responses

Action endpoints map errors to standard HTTP codes:

| Status | When |
|---|---|
| `400` | Serializer validation failed (e.g. missing required field), or the underlying Wagtail action raised `django.core.exceptions.ValidationError`. |
| `403` | Wagtail's per-page permission check failed (e.g. user cannot publish the destination page). |
| `404` | Unknown page id, unknown action name, or a referenced resource (revision, locale, destination) does not exist. |
| `405` | Wrong HTTP method (action endpoints are POST only). |

```{note}
Permission failures from the underlying action classes are surfaced as `403 {"detail": "..."}` when the action raises a subclass of `django.core.exceptions.PermissionDenied`, but as `400 {"<field>": [...]}` when it raises a plain `ValidationError`. Catch both when consuming the API.
```

(api_v2_register_page_api_action)=

## Adding your own action

Wagtail exposes a `register_page_api_action` hook so packages and projects can extend the action list without subclassing `PagesAdminAPIViewSet`.

### 1. Subclass `APIAction`

`wagtail.admin.api.actions.base.APIAction` is the contract every action implements:

```python
from rest_framework import fields, status
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from wagtail.admin.api.actions.base import APIAction


class ArchivePageAPIActionSerializer(Serializer):
    reason = fields.CharField(required=False, allow_blank=True)


class ArchivePageAPIAction(APIAction):
    serializer = ArchivePageAPIActionSerializer

    def execute(self, instance, data):
        if not instance.permissions_for_user(self.request.user).can_publish():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You cannot archive this page.")

        instance.archive_reason = data.get("reason", "")
        instance.archived = True
        instance.save(update_fields=["archived", "archive_reason"])

        serializer = self.view.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)
```

A few conventions to follow:

- Set `serializer` to a DRF `Serializer` class for input validation. If your action takes no input, use the base `Serializer` (an empty body will validate).
- Permission failures should raise DRF's `PermissionDenied` (→ `403`) or Django's `PermissionDenied` subclasses, not bare exceptions.
- Business-rule failures should raise `rest_framework.exceptions.ValidationError` (→ `400`) or `wagtail.api.v2.utils.BadRequestError`.
- On success, return the serialized page through `self.view.get_serializer(instance)` so the response shape matches the rest of the admin API.

### 2. Register via the hook

In your app's `wagtail_hooks.py`:

```python
from wagtail import hooks

from myapp.api_actions import ArchivePageAPIAction


@hooks.register("register_page_api_action")
def register_archive_action():
    return ("archive", ArchivePageAPIAction)
```

After this, `POST /admin/api/main/pages/<id>/action/archive/` will dispatch to `ArchivePageAPIAction`.

```{note}
Hook-registered actions are merged on top of the built-in `actions` dict on `PagesAdminAPIViewSet`. If you register an action with a name that already exists, your action overrides the built-in.
```

### 3. Built-in `lock` and `unlock` as worked examples

`lock` and `unlock` (added in Wagtail 8.0) are good references — they're short, perform a permission check, and use the same pattern third-party actions should follow. See `wagtail/admin/api/actions/lock.py`.

## Testing your action

Tests should reverse the URL by its router-namespaced name and post via Django's test client. See `wagtail/admin/tests/api/test_pages.py` for examples; the relevant classes are `TestCopyPageAction`, `TestLockPageAction`, `TestPageActionDispatch`, and `TestRegisterPageAPIActionHook`.

```python
from django.test import TestCase
from django.urls import reverse

from wagtail.admin.tests.api.utils import AdminAPITestCase


class TestArchivePageAction(AdminAPITestCase, TestCase):
    fixtures = ["test.json"]

    def test_archive(self):
        response = self.client.post(
            reverse("wagtailadmin_api:pages:action", args=[3, "archive"]),
            data={"reason": "outdated"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
```
