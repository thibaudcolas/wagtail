"""
OpenAPI parameter descriptions for Wagtail's API v2 query strings.

Each constant is a list of ``OpenApiParameter`` objects that can be passed
to ``@extend_schema(parameters=...)``. They are grouped so that extensions
attach only the parameters that actually apply to a given action/endpoint.
"""

from drf_spectacular.utils import OpenApiParameter

# Parameters accepted by every listing endpoint (BaseAPIViewSet.known_query_parameters).
COMMON_LISTING_PARAMETERS = [
    OpenApiParameter(
        name="limit",
        type=int,
        location=OpenApiParameter.QUERY,
        description=(
            "Maximum number of results to return. Capped by the "
            "``WAGTAILAPI_LIMIT_MAX`` setting (default 20)."
        ),
    ),
    OpenApiParameter(
        name="offset",
        type=int,
        location=OpenApiParameter.QUERY,
        description="Number of results to skip before returning the page of results.",
    ),
    OpenApiParameter(
        name="fields",
        type=str,
        location=OpenApiParameter.QUERY,
        description=(
            "Comma-separated list of fields to include in the response. "
            "Prefix with ``*`` to start from all available fields, ``_`` to "
            "start from none, or ``-`` to remove a default field. "
            "See the API usage docs for the full syntax."
        ),
    ),
    OpenApiParameter(
        name="order",
        type=str,
        location=OpenApiParameter.QUERY,
        description=(
            "Field name to order by. Prefix with ``-`` for descending order, "
            "or use ``random`` for a random ordering."
        ),
    ),
    OpenApiParameter(
        name="search",
        type=str,
        location=OpenApiParameter.QUERY,
        description="Full-text search query. Disabled when ``WAGTAILAPI_SEARCH_ENABLED`` is False.",
    ),
    OpenApiParameter(
        name="search_operator",
        type=str,
        location=OpenApiParameter.QUERY,
        enum=["and", "or"],
        description="Combine search terms with AND (default) or OR.",
    ),
]

# Page-specific parameters, in addition to the common ones.
PAGE_LISTING_PARAMETERS = [
    OpenApiParameter(
        name="type",
        type=str,
        location=OpenApiParameter.QUERY,
        description=(
            "Restrict results to one or more page types, given as "
            "``app_label.ModelName`` (comma-separated for multiple). "
            "Required to filter on or return type-specific fields."
        ),
    ),
    OpenApiParameter(
        name="child_of",
        type=int,
        location=OpenApiParameter.QUERY,
        description="Only return direct children of the given page id.",
    ),
    OpenApiParameter(
        name="ancestor_of",
        type=int,
        location=OpenApiParameter.QUERY,
        description="Only return ancestors of the given page id.",
    ),
    OpenApiParameter(
        name="descendant_of",
        type=int,
        location=OpenApiParameter.QUERY,
        description="Only return descendants of the given page id.",
    ),
    OpenApiParameter(
        name="translation_of",
        type=int,
        location=OpenApiParameter.QUERY,
        description="Only return translations of the given page id (requires WAGTAIL_I18N_ENABLED).",
    ),
    OpenApiParameter(
        name="locale",
        type=str,
        location=OpenApiParameter.QUERY,
        description="Restrict results to a single locale, by language code.",
    ),
    OpenApiParameter(
        name="site",
        type=str,
        location=OpenApiParameter.QUERY,
        description=(
            "Restrict results to a single site, by hostname or ``hostname:port``. "
            "Defaults to the site matching the current request."
        ),
    ),
]

# Find endpoint accepts the ``id`` query parameter on every endpoint, and
# ``html_path`` additionally on pages.
FIND_PARAMETERS = [
    OpenApiParameter(
        name="id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="Look up an object by its primary key.",
    ),
]

PAGE_FIND_PARAMETERS = FIND_PARAMETERS + [
    OpenApiParameter(
        name="html_path",
        type=str,
        location=OpenApiParameter.QUERY,
        description="Look up a page by its public URL path (eg. ``/blog/my-post/``).",
    ),
]
