"""Isolated PostgreSQL tests, independent of serving database credentials."""

import os

from .test_settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "jengasec_test",
        "USER": os.environ.get("JENGASEC_TEST_PG_USER", "postgres"),
        "PASSWORD": os.environ.get("JENGASEC_TEST_PG_PASSWORD", ""),
        "HOST": os.environ.get("JENGASEC_TEST_PG_HOST", "127.0.0.1"),
        "PORT": os.environ.get("JENGASEC_TEST_PG_PORT", "5432"),
        "TEST": {"NAME": "test_jengasec"},
    }
}
