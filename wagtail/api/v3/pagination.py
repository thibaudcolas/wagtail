from django.conf import settings

from .utils import BadRequestError


def paginate(queryset, request):
    """
    Slice a queryset using ``?offset`` and ``?limit`` query parameters and
    return ``(items, total_count)``.

    Matches the v2 envelope: callers wrap the result into
    ``{"meta": {"total_count": N}, "items": [...]}``.
    """
    limit_max = getattr(settings, "WAGTAILAPI_LIMIT_MAX", 20)

    try:
        offset = int(request.GET.get("offset", 0))
        if offset < 0:
            raise ValueError
    except ValueError as e:
        raise BadRequestError("offset must be a positive integer") from e

    try:
        limit_default = 20 if not limit_max else min(20, limit_max)
        limit = int(request.GET.get("limit", limit_default))
        if limit < 0:
            raise ValueError
    except ValueError as e:
        raise BadRequestError("limit must be a positive integer") from e

    if limit_max and limit > limit_max:
        raise BadRequestError("limit cannot be higher than %d" % limit_max)

    total_count = queryset.count()
    return list(queryset[offset : offset + limit]), total_count
