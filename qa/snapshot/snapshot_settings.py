"""Settings for the snapshot QA prototype.

Extends the regular Wagtail test settings and adds drf-spectacular + the
optional schema extension so ``manage.py spectacular`` can produce a real
schema.
"""

from wagtail.test.settings import *  # noqa: F401,F403

INSTALLED_APPS = INSTALLED_APPS + [  # noqa: F405
    "drf_spectacular",
    "wagtail.api.v2.schema",
]

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Wagtail API",
    "VERSION": "v2",
    # Keep generation reproducible: sort components alphabetically and turn
    # off any setting that pulls in non-deterministic data (timestamps, etc).
    "SORT_OPERATIONS": True,
}
