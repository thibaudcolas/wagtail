"""
``/api/v3/pages/`` router.

Read endpoints accept anonymous callers (using the v2 read semantics —
``api_fields`` whitelist) while still honouring tokens and sessions for
authenticated reads that want to see every editable field. Write endpoints
require a bearer token or session and run all operations through Wagtail's
``PagePermissionPolicy``.
"""

from collections import OrderedDict

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from ninja import Body, Router
from ninja.errors import AuthenticationError

from wagtail.models import Page

from ..auth import resolve_optional_user, write_auth
from ..filters import (
    KNOWN_PAGE_QUERY_PARAMS,
    apply_search,
    filter_by_fields,
    filter_by_locale,
    filter_by_site,
    filter_by_tree,
    filter_by_type,
    order_queryset,
)
from ..pagination import paginate
from ..permissions import (
    can_add_subpage,
    can_change,
    can_delete,
    can_publish,
)
from ..schemas import (
    PAGE_BODY_FIELDS,
    PAGE_META_FIELDS,
    apply_payload,
    get_available_fields,
    save_child_payloads,
    serialize_page,
)
from ..utils import BadRequestError, page_models_from_string


PAGES_PATH = "/api/v3/pages/"


def _base_queryset(request):
    return Page.objects.all().filter(depth__gte=1)


def _list_queryset(request):
    user = resolve_optional_user(request)
    qs = Page.objects.all()
    if not (user and user.is_authenticated):
        # Anonymous callers see only live, public pages.
        qs = qs.live().public()
    return qs


def _resolve_specific(page) -> Page:
    return page.specific if hasattr(page, "specific") else page


def _ensure_authenticated(request) -> Page:
    user = getattr(request, "auth", None) or getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        raise AuthenticationError
    request.user = user
    return user


def build_pages_router() -> Router:
    router = Router()

    @router.get("", auth=None)
    def list_pages(request):
        user = resolve_optional_user(request)
        authenticated = user is not None and user.is_authenticated

        # Validate ``?type=`` early; an invalid value yields 400 rather than 200.
        type_str = request.GET.get("type")
        if type_str:
            try:
                models = page_models_from_string(type_str)
            except (LookupError, ValueError) as e:
                raise BadRequestError(f"type doesn't exist: {e}") from e
        else:
            models = []

        queryset = _list_queryset(request)
        queryset = filter_by_site(queryset, request)
        queryset = filter_by_tree(queryset, request)
        queryset = filter_by_locale(queryset, request)
        queryset = filter_by_type(queryset, request)

        # When a single page type was requested, switch queryset to that model
        # so its custom fields become filterable.
        if len(models) == 1:
            target_model = models[0]
        else:
            target_model = Page

        available_fields = get_available_fields(
            target_model, authenticated=authenticated, is_page=True
        )
        queryset = filter_by_fields(queryset, request, available_fields)
        # Ordering must run *before* search: search backends return a
        # SearchResults wrapper that cannot be further ordered (the same
        # constraint v2's SearchFilter documents, hence why it sits last in
        # the v2 filter_backends chain).
        queryset = order_queryset(queryset, request)
        queryset = apply_search(queryset, request)

        items, total_count = paginate(queryset, request)
        serialized = [
            serialize_page(
                item,
                request=request,
                authenticated=authenticated,
                endpoint_path=PAGES_PATH,
            )
            for item in items
        ]
        return OrderedDict(
            [
                ("meta", OrderedDict([("total_count", total_count)])),
                ("items", serialized),
            ]
        )

    @router.get("find/", auth=None)
    def find_page(request):
        from wagtail.models import Site

        user = resolve_optional_user(request)
        authenticated = user is not None and user.is_authenticated
        queryset = _list_queryset(request)
        page = None
        if html_path := request.GET.get("html_path"):
            site = Site.find_for_request(request)
            if site is not None:
                components = [c for c in html_path.split("/") if c]
                # Resolve .specific defensively — a missing specific row
                # would otherwise turn a routing question into a 500.
                try:
                    root = site.root_page.specific
                except Exception:
                    root = site.root_page
                try:
                    page, _, _ = root.route(request, components)
                except Http404:
                    page = None
        elif page_id := request.GET.get("id"):
            try:
                page = queryset.get(pk=int(page_id))
            except (ValueError, Page.DoesNotExist) as e:
                raise Http404("page not found") from e

        if page is None or not queryset.filter(pk=page.pk).exists():
            raise Http404("page not found")

        from django.http import HttpResponseRedirect

        return HttpResponseRedirect(
            request.build_absolute_uri(f"{PAGES_PATH}{page.pk}/")
        )

    @router.get("{int:page_id}/", auth=None)
    def get_page(request, page_id: int):
        user = resolve_optional_user(request)
        authenticated = user is not None and user.is_authenticated
        queryset = _list_queryset(request)
        try:
            page = queryset.get(pk=page_id)
        except Page.DoesNotExist as e:
            raise Http404("page not found") from e
        return serialize_page(
            page,
            request=request,
            authenticated=authenticated,
            endpoint_path=PAGES_PATH,
            detail=True,
        )

    # ------------------------------------------------------------------
    # Write endpoints
    # ------------------------------------------------------------------

    @router.post("", auth=write_auth())
    def create_page(request, payload: dict = Body(...)):
        user = _ensure_authenticated(request)
        if not isinstance(payload, dict):
            raise BadRequestError("payload must be a JSON object")

        type_str = payload.pop("type", None)
        parent_id = payload.pop("parent", None)
        if not type_str:
            raise BadRequestError("'type' is required (e.g. 'app.PageModel')")
        if parent_id is None:
            raise BadRequestError("'parent' page id is required")

        try:
            (model_cls,) = page_models_from_string(type_str)
        except (LookupError, ValueError) as e:
            raise BadRequestError(f"unknown page type: {e}") from e

        try:
            parent = Page.objects.get(pk=int(parent_id)).specific
        except (ValueError, Page.DoesNotExist) as e:
            raise BadRequestError("parent page does not exist") from e

        if not can_add_subpage(user, parent):
            raise PermissionDenied("user may not add a subpage here")

        instance = model_cls()
        apply_payload(instance, payload)
        if not instance.title:
            raise BadRequestError("'title' is required")
        if not instance.slug:
            from django.utils.text import slugify

            instance.slug = slugify(instance.title)
        instance.live = False
        instance.has_unpublished_changes = True
        parent.add_child(instance=instance)
        save_child_payloads(instance)
        instance.save_revision(user=user)

        response = serialize_page(
            instance,
            request=request,
            authenticated=True,
            endpoint_path=PAGES_PATH,
            detail=True,
        )
        return JsonResponse(response, status=201)

    @router.patch("{int:page_id}/", auth=write_auth())
    def update_page(request, page_id: int, payload: dict = Body(...)):
        user = _ensure_authenticated(request)
        page = _get_writable_page(page_id)
        if not can_change(user, page):
            raise PermissionDenied("user may not change this page")
        result = apply_payload(page, payload)
        page.save()
        save_child_payloads(page)
        revision = page.save_revision(user=user)
        response = serialize_page(
            page,
            request=request,
            authenticated=True,
            endpoint_path=PAGES_PATH,
            detail=True,
        )
        response["meta"] = response.get("meta") or OrderedDict()
        response["meta"]["latest_revision"] = revision.pk
        response["meta"]["applied_fields"] = result["applied"]
        if result["skipped"]:
            response["meta"]["skipped_fields"] = result["skipped"]
        return response

    @router.delete("{int:page_id}/", auth=write_auth())
    def delete_page(request, page_id: int):
        user = _ensure_authenticated(request)
        page = _get_writable_page(page_id)
        if not can_delete(user, page):
            raise PermissionDenied("user may not delete this page")
        page.delete(user=user)
        return HttpResponse(status=204)

    @router.post("{int:page_id}/publish/", auth=write_auth())
    def publish_page(request, page_id: int):
        user = _ensure_authenticated(request)
        page = _get_writable_page(page_id)
        if not can_publish(user, page):
            raise PermissionDenied("user may not publish this page")
        revision = page.save_revision(user=user)
        revision.publish(user=user)
        page.refresh_from_db()
        return serialize_page(
            page.specific,
            request=request,
            authenticated=True,
            endpoint_path=PAGES_PATH,
            detail=True,
        )

    @router.post("{int:page_id}/unpublish/", auth=write_auth())
    def unpublish_page(request, page_id: int):
        user = _ensure_authenticated(request)
        page = _get_writable_page(page_id)
        if not can_publish(user, page):
            raise PermissionDenied("user may not unpublish this page")
        page.unpublish(user=user)
        page.refresh_from_db()
        return serialize_page(
            page.specific,
            request=request,
            authenticated=True,
            endpoint_path=PAGES_PATH,
            detail=True,
        )

    @router.post("{int:page_id}/copy/", auth=write_auth())
    def copy_page(request, page_id: int, payload: dict = Body(default={})):
        user = _ensure_authenticated(request)
        page = _get_writable_page(page_id)
        payload = payload or {}
        destination_id = payload.get("destination")
        if destination_id is None:
            destination = page.get_parent()
        else:
            try:
                destination = Page.objects.get(pk=int(destination_id)).specific
            except (ValueError, Page.DoesNotExist) as e:
                raise BadRequestError("destination page does not exist") from e
        if not can_add_subpage(user, destination):
            raise PermissionDenied("user may not add a subpage in the destination")
        new_page = page.copy(
            recursive=payload.get("recursive", False),
            to=destination,
            update_attrs=payload.get("update_attrs") or None,
            user=user,
            keep_live=False,
        )
        return JsonResponse(
            serialize_page(
                new_page.specific,
                request=request,
                authenticated=True,
                endpoint_path=PAGES_PATH,
                detail=True,
            ),
            status=201,
        )

    @router.post("{int:page_id}/move/", auth=write_auth())
    def move_page(request, page_id: int, payload: dict = Body(...)):
        user = _ensure_authenticated(request)
        page = _get_writable_page(page_id)
        if "destination" not in payload:
            raise BadRequestError("'destination' page id is required")
        try:
            destination = Page.objects.get(pk=int(payload["destination"])).specific
        except (ValueError, Page.DoesNotExist) as e:
            raise BadRequestError("destination page does not exist") from e
        if not can_change(user, page) or not can_add_subpage(user, destination):
            raise PermissionDenied("user may not move this page to the destination")
        position = payload.get("position", "last-child")
        page.move(destination, pos=position, user=user)
        page.refresh_from_db()
        return serialize_page(
            page.specific,
            request=request,
            authenticated=True,
            endpoint_path=PAGES_PATH,
            detail=True,
        )

    return router


def _get_writable_page(page_id: int) -> Page:
    try:
        return Page.objects.get(pk=page_id).specific
    except Page.DoesNotExist as e:
        raise Http404("page not found") from e
