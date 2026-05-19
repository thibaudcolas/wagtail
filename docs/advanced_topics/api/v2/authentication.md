(api_v2_authentication)=

# Authenticating clients of the Wagtail API

The Wagtail API v2 endpoints (and Wagtail's session-authenticated admin API) are Django REST framework `ViewSet`s, so any of [DRF's authentication backends](https://www.django-rest-framework.org/api-guide/authentication/) can be plugged in. This page walks through the realistic options when exposing the API beyond a single first-party site.

```{note}
"Authentication" is *who* is calling. "Authorization" — what they can do — is a separate concern. Wagtail's per-page `Page.permissions_for_user(user)` checks run regardless of how the user authenticated; if you wire up token or OAuth auth, make sure each token still resolves to a Django user with appropriate group memberships and `wagtailadmin.access_admin`.
```

## What ships by default

| Endpoint | Authentication | Notes |
|---|---|---|
| `/api/v2/...` (public, configured by the developer) | None | Anonymous read-only. |
| `/admin/api/main/...` (Wagtail's admin API) | `SessionAuthentication` | Same cookie session as the Wagtail admin. State-changing requests must include the CSRF token. |

The public endpoints are registered explicitly by the developer (see [](api_v2_configure_endpoints)), so the authentication setup is per-endpoint.

## Option 1: Session authentication

Use this when the API is consumed by code that runs in the same browser session as the admin (the React explorer, server-rendered admin views, etc.).

- `SessionAuthentication` enforces CSRF on unsafe methods.
- Clients must send the `csrftoken` cookie value back in an `X-CSRFToken` header and a same-origin `Referer`.
- For server-to-server traffic, session auth is awkward — prefer tokens or OAuth.

No setup is needed beyond what Wagtail already does for `/admin/api/`. To enable it on a public endpoint:

```python
class CustomPagesAPIViewSet(PagesAPIViewSet):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
```

## Option 2: Token authentication (DRF built-in)

This is the simplest "give a script credentials" approach. It hands out long-lived bearer tokens stored in Django's database.

Install:

```python
# settings.py
INSTALLED_APPS += [
    "rest_framework.authtoken",
]
```

Run migrations:

```bash
./manage.py migrate
```

Enable on the endpoint:

```python
# api.py
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from wagtail.api.v2.views import PagesAPIViewSet


class CustomPagesAPIViewSet(PagesAPIViewSet):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]


api_router.register_endpoint("pages", CustomPagesAPIViewSet)
```

Create a token for a user:

```bash
./manage.py drf_create_token <username>
```

Call the API:

```bash
curl https://example.com/api/v2/pages/ \
  -H "Authorization: Token exampleSecretToken123xyz"
```

Trade-offs:

- **Pros:** Built into DRF. Trivial to manage from Django admin.
- **Cons:** Tokens are long-lived and not scoped. Anyone with the token has the full permissions of the underlying user, with no expiry, no audience restriction, and no easy rotation. Always serve token-authenticated APIs over HTTPS.

## Option 3: Knox / JWT / signed token libraries

If you need expiring or per-device tokens but don't want a full OAuth server, drop in one of:

- [`django-rest-knox`](https://jazzband.github.io/django-rest-knox/) — short-lived tokens, per-device sessions, server-side revocation. Sensible default for first-party mobile apps and SPAs.
- [`djangorestframework-simplejwt`](https://django-rest-framework-simplejwt.readthedocs.io/) — stateless JSON Web Tokens. Good for service-to-service traffic where you'd rather not hit the database to validate every request.

Both register their own DRF authentication class; the wiring on Wagtail's side is identical to Option 2 — just swap `TokenAuthentication` for the library's class.

```python
from knox.auth import TokenAuthentication as KnoxTokenAuthentication


class CustomPagesAPIViewSet(PagesAPIViewSet):
    authentication_classes = [KnoxTokenAuthentication]
    permission_classes = [IsAuthenticated]
```

## Option 4: OAuth2 / OIDC

For multi-tenant, third-party, or "log in with Google" scenarios, run an OAuth2 provider (or delegate to one) and authenticate API calls with the resulting access tokens.

The two common Django integrations are:

- [`django-oauth-toolkit`](https://django-oauth-toolkit.readthedocs.io/) — run your own OAuth2 provider in Django, hand out scopes, manage applications. Ships with a DRF `OAuth2Authentication` class.
- [`mozilla-django-oidc`](https://mozilla-django-oidc.readthedocs.io/) — defer to an external identity provider (Auth0, Keycloak, Google, Azure AD, …) and accept OIDC ID tokens.

### `django-oauth-toolkit` quick wiring

```python
# settings.py
INSTALLED_APPS += ["oauth2_provider"]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "oauth2_provider.contrib.rest_framework.OAuth2Authentication",
    ],
}
```

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    path("o/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    # ...your Wagtail urls...
]
```

Then per endpoint:

```python
from oauth2_provider.contrib.rest_framework import (
    OAuth2Authentication,
    TokenHasScope,
)


class CustomPagesAPIViewSet(PagesAPIViewSet):
    authentication_classes = [OAuth2Authentication]
    permission_classes = [TokenHasScope]
    required_scopes = ["read:pages"]
```

A client first obtains an access token via one of the OAuth2 flows (authorization code with PKCE for user-facing apps, client credentials for service-to-service), then includes it as a Bearer token:

```bash
curl https://example.com/api/v2/pages/ \
  -H "Authorization: Bearer eyJhbGciOi..."
```

### When to pick OAuth

OAuth is overkill for "one mobile app that talks to one CMS". Reach for it when:

- Multiple client applications consume the same API and you want to revoke them independently.
- You need scope-based authorization (`read:pages` vs `write:pages`).
- Users need to authorize a third party to act on their behalf without sharing credentials.
- You're consolidating identity across multiple services and want SSO.

## Permissions, not just authentication

Authentication ties the request to a Django user. To control what that user can do, layer DRF `permission_classes` on top:

- `IsAuthenticated` — must be signed in.
- `DjangoModelPermissions` — defer to Django's per-model `add_*` / `change_*` / `delete_*` permissions.
- `DjangoObjectPermissions` — extend to per-object permissions (works with [`django-guardian`](https://django-guardian.readthedocs.io/) if you need per-instance ACLs).

For the admin API's write actions (see [](api_v2_admin_actions)) the authorization layer is mostly inside each `wagtail.actions.*` class — they all consult `page.permissions_for_user(user).can_*` and raise `PermissionDenied` when the user isn't allowed. As long as your authentication layer resolves the request to the correct Django user, Wagtail's per-page permission model continues to apply.

## CSRF and CORS

- Session-authenticated clients **need** CSRF. Token, OAuth, and JWT auth bypass DRF's CSRF check by design — only session auth opts in.
- Cross-origin clients also need the server to set appropriate CORS headers. [`django-cors-headers`](https://github.com/adamchainz/django-cors-headers) is the standard solution; configure `CORS_ALLOWED_ORIGINS` to include the API consumer's origin and avoid `CORS_ALLOW_ALL_ORIGINS = True` outside development.

## Always serve over HTTPS

Bearer tokens of any kind (DRF's `Token`, Knox, JWT, OAuth access tokens) are credentials. A leaked token is equivalent to a leaked password. The minimum protection is TLS on every endpoint that issues or accepts a token.
