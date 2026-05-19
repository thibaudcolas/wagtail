from wagtail.models import Page
from wagtail.permission_policies.pages import PagePermissionPolicy


_policy = PagePermissionPolicy(Page)


def can_add_subpage(user, parent_page) -> bool:
    return _policy.user_has_permission_for_instance(user, "add", parent_page)


def can_change(user, page) -> bool:
    return _policy.user_has_permission_for_instance(user, "change", page)


def can_publish(user, page) -> bool:
    return _policy.user_has_permission_for_instance(user, "publish", page)


def can_delete(user, page) -> bool:
    return _policy.user_has_permission_for_instance(user, "delete", page)


def visible_pages(user):
    """Queryset of pages visible to ``user`` (live + drafts they can see)."""
    if user is not None and user.is_authenticated:
        return _policy.instances_user_has_permission_for(user, "change") | Page.objects.live()
    return Page.objects.live().public()
