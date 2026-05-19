from django.contrib.auth.models import AnonymousUser
from ninja.security import HttpBearer
from ninja.security.session import SessionAuth

from .models import ApiToken


def _resolve_token(request, token):
    try:
        api_token = ApiToken.objects.select_related("user").get(
            key=token, revoked=False
        )
    except ApiToken.DoesNotExist:
        return None
    user = api_token.user
    if not user.is_active:
        return None
    api_token.touch()
    return user


class BearerTokenAuth(HttpBearer):
    """Required bearer token auth used by write endpoints."""

    def authenticate(self, request, token):
        user = _resolve_token(request, token)
        if user is None:
            return None
        request.user = user
        return user


def resolve_optional_user(request):
    """
    Look at the request and return the authenticated user, if any.

    Tries the ``Authorization: Bearer <key>`` header first, then falls back
    to ``request.user`` set by Django's session middleware. Used by read
    endpoints that allow anonymous access but want to expose extra fields
    to authenticated callers.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer ") :].strip()
        user = _resolve_token(request, token)
        if user is not None:
            request.user = user
            return user

    user = getattr(request, "user", None)
    if user is None or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return None
    return user


bearer_auth = BearerTokenAuth()
session_auth = SessionAuth()


def write_auth():
    """Auth chain for write operations: bearer token or Django session."""
    return [bearer_auth, session_auth]
