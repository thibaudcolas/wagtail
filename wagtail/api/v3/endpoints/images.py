"""``/api/v3/images/`` router (read-only in this initial slice)."""

from collections import OrderedDict

from django.http import Http404
from ninja import Router

from wagtail.images import get_image_model

from ..auth import resolve_optional_user
from ..pagination import paginate
from ..schemas import serialize_instance


IMAGES_PATH = "/api/v3/images/"


def build_images_router() -> Router:
    router = Router()
    Image = get_image_model()

    @router.get("", auth=None)
    def list_images(request):
        user = resolve_optional_user(request)
        authenticated = user is not None and user.is_authenticated
        queryset = Image.objects.all().order_by("id")
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
                            endpoint_path=IMAGES_PATH,
                        )
                        for item in items
                    ],
                ),
            ]
        )

    @router.get("{int:image_id}/", auth=None)
    def get_image(request, image_id: int):
        user = resolve_optional_user(request)
        authenticated = user is not None and user.is_authenticated
        try:
            image = Image.objects.get(pk=image_id)
        except Image.DoesNotExist as e:
            raise Http404("image not found") from e
        return serialize_instance(
            image,
            request=request,
            authenticated=authenticated,
            endpoint_path=IMAGES_PATH,
            detail=True,
        )

    return router
