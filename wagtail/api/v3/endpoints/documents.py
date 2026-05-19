"""``/api/v3/documents/`` router (read-only in this initial slice)."""

from collections import OrderedDict

from django.http import Http404
from ninja import Router

from wagtail.documents import get_document_model

from ..auth import resolve_optional_user
from ..pagination import paginate
from ..schemas import serialize_instance


DOCUMENTS_PATH = "/api/v3/documents/"


def build_documents_router() -> Router:
    router = Router()
    Document = get_document_model()

    @router.get("", auth=None)
    def list_documents(request):
        user = resolve_optional_user(request)
        authenticated = user is not None and user.is_authenticated
        queryset = Document.objects.all().order_by("id")
        items, total_count = paginate(queryset, request)
        return OrderedDict(
            [
                ("meta", OrderedDict([("total_count", total_count)])),
                (
                    "items",
                    [
                        serialize_instance(
                            item,
                            request=request,
                            authenticated=authenticated,
                            endpoint_path=DOCUMENTS_PATH,
                        )
                        for item in items
                    ],
                ),
            ]
        )

    @router.get("{int:document_id}/", auth=None)
    def get_document(request, document_id: int):
        user = resolve_optional_user(request)
        authenticated = user is not None and user.is_authenticated
        try:
            document = Document.objects.get(pk=document_id)
        except Document.DoesNotExist as e:
            raise Http404("document not found") from e
        return serialize_instance(
            document,
            request=request,
            authenticated=authenticated,
            endpoint_path=DOCUMENTS_PATH,
            detail=True,
        )

    return router
