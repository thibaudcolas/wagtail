"""
Factory for the v3 :class:`ninja.NinjaAPI` instance.

The factory shape mirrors v2's :class:`WagtailAPIRouter` so projects can wire
endpoints in their own ``urls.py``::

    from wagtail.api.v3.api import build_api

    api = build_api()
    urlpatterns = [path("api/v3/", api.urls)]

Custom endpoints can be attached before mounting by calling
``api.add_router("/foo/", my_router)``.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from ninja import NinjaAPI
from ninja.errors import AuthenticationError

from .endpoints.documents import build_documents_router
from .endpoints.images import build_images_router
from .endpoints.pages import build_pages_router
from .utils import BadRequestError


def build_api(*, version: str = "3.0.0", urls_namespace: str = "wagtailapi_v3") -> NinjaAPI:
    api = NinjaAPI(
        title="Wagtail API",
        version=version,
        urls_namespace=urls_namespace,
        description=(
            "Read/write API for Wagtail content. Read endpoints accept "
            "anonymous requests; write endpoints require either a session "
            "cookie or an `Authorization: Bearer <token>` header."
        ),
    )

    api.add_router("/pages/", build_pages_router())
    api.add_router("/images/", build_images_router())
    api.add_router("/documents/", build_documents_router())

    @api.exception_handler(BadRequestError)
    def _bad_request(request, exc):
        return api.create_response(request, {"message": str(exc)}, status=400)

    @api.exception_handler(Http404)
    def _not_found(request, exc):
        return api.create_response(request, {"message": str(exc) or "not found"}, status=404)

    @api.exception_handler(PermissionDenied)
    def _permission_denied(request, exc):
        return api.create_response(request, {"message": str(exc) or "forbidden"}, status=403)

    @api.exception_handler(AuthenticationError)
    def _auth_error(request, exc):
        return api.create_response(request, {"message": "authentication required"}, status=401)

    @api.exception_handler(ValidationError)
    def _validation_error(request, exc):
        if hasattr(exc, "message_dict"):
            payload = exc.message_dict
        else:
            payload = {"message": "; ".join(exc.messages)}
        return api.create_response(request, payload, status=400)

    return api
