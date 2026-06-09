"""Settings for the schemathesis QA prototype.

Extends the UI-test settings (which uses a persistent SQLite db with the
demosite fixture loaded) and adds drf-spectacular + the schema extension
plus a URL route serving the OpenAPI document at ``/api/schema/``.
"""

from wagtail.test.settings_ui import *  # noqa: F401,F403

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
}

# Replace the test ROOT_URLCONF with one that adds /api/schema/. Defined
# below in qa/schemathesis/schemathesis_urls.py so it can import the
# regular Wagtail URL conf and append the spectacular view.
ROOT_URLCONF = "schemathesis_urls"
