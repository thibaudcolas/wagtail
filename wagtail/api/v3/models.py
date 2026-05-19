import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def _generate_key():
    return secrets.token_urlsafe(32)


class ApiToken(models.Model):
    """
    A bearer token tied to a Django user. Presented in the
    ``Authorization: Bearer <key>`` header for write-API access.
    """

    key = models.CharField(
        _("key"), max_length=128, unique=True, default=_generate_key, editable=False
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="wagtail_api_tokens",
        on_delete=models.CASCADE,
        verbose_name=_("user"),
    )
    label = models.CharField(_("label"), max_length=255, blank=True)
    created = models.DateTimeField(_("created"), default=timezone.now)
    last_used = models.DateTimeField(_("last used"), null=True, blank=True)
    revoked = models.BooleanField(_("revoked"), default=False)

    class Meta:
        verbose_name = _("API token")
        verbose_name_plural = _("API tokens")

    def __str__(self):
        return self.label or f"token-{self.pk}"

    def touch(self):
        """Update the ``last_used`` timestamp without dispatching signals."""
        ApiToken.objects.filter(pk=self.pk).update(last_used=timezone.now())
