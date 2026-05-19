import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import wagtail.api.v3.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiToken",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "key",
                    models.CharField(
                        default=wagtail.api.v3.models._generate_key,
                        editable=False,
                        max_length=128,
                        unique=True,
                        verbose_name="key",
                    ),
                ),
                ("label", models.CharField(blank=True, max_length=255, verbose_name="label")),
                (
                    "created",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="created"
                    ),
                ),
                (
                    "last_used",
                    models.DateTimeField(blank=True, null=True, verbose_name="last used"),
                ),
                ("revoked", models.BooleanField(default=False, verbose_name="revoked")),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="wagtail_api_tokens",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="user",
                    ),
                ),
            ],
            options={
                "verbose_name": "API token",
                "verbose_name_plural": "API tokens",
            },
        ),
    ]
