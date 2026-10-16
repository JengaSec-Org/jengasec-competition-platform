"""Isolated test storage and fast password hashing; never a serving profile."""

import tempfile

from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# Document parsers need real paths. Keep their files outside serving storage
# and let TemporaryDirectory remove this test process's uploads on exit.
_test_media = tempfile.TemporaryDirectory(prefix="jengasec-tests-")
MEDIA_ROOT = _test_media.name
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
