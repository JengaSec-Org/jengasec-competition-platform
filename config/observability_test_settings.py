"""Isolated databases, disk files and captured email for interface tests."""
import atexit
import os
from pathlib import Path
import shutil
import tempfile

from .settings import *  # noqa: F403

_directory = tempfile.mkdtemp(prefix="jengasec-interface-tests-")
atexit.register(shutil.rmtree, _directory, ignore_errors=True)
MEDIA_ROOT = Path(_directory) / "media"
STATIC_ROOT = Path(_directory) / "static"
MEDIA_ROOT.mkdir()
STATIC_ROOT.mkdir()
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CRON_TOKEN = ""

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": Path(_directory) / "test.sqlite3"}}
if os.environ.get("OBS_TEST_PG_SOCKET"):
    socket = Path(os.environ["OBS_TEST_PG_SOCKET"])
    if not socket.is_absolute() or not socket.is_dir():
        raise ValueError("OBS_TEST_PG_SOCKET must name a disposable local test socket directory")
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql", "NAME": "obs_interface",
        "USER": "obs_test", "HOST": str(socket), "PORT": "5432",
    }}
elif os.environ.get("OBS_TEST_PG_HOST"):
    host = os.environ["OBS_TEST_PG_HOST"]
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("OBS_TEST_PG_HOST must be a loopback test service")
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql", "NAME": "obs_interface",
        "USER": "obs_test", "PASSWORD": os.environ.get("OBS_TEST_PG_PASSWORD", ""),
        "HOST": host, "PORT": "5432", "OPTIONS": {"sslmode": "disable"},
    }}
