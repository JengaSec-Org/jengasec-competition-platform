"""
Django settings for the JengaSec Evaluation Platform.

Architecture: Browser -> Templates -> Views -> Services -> Models -> PostgreSQL
Business logic lives in `services/`, not in views.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-change-me-before-deployment",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# Render sets this to the service's public hostname (e.g. jengasec.onrender.com).
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# Django refuses POSTs (login, forms) from an origin it does not trust. Behind
# a TLS-terminating proxy (Render, Cloudflare, nginx) that origin is https://,
# so every public hostname must be listed here with its scheme.
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o
]
if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

# Behind a proxy Django only sees plain HTTP; this header is how it learns the
# original request was HTTPS (needed for secure cookies and is_secure()).
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # JengaSec modules
    "accounts",
    "competitions",
    "submissions",
    "judging",
    "rubrics",
    "ai_engine",
    "reports",
    "analytics",
    "notifications",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves the collected static files from the app process itself, so no
    # nginx is needed in front (Render). Must sit right after SecurityMiddleware.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.user_roles",
                "notifications.context_processors.unread_notification_count",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# Default: SQLite so anyone can run the project immediately.
# For the shared test DB (Neon) or production PostgreSQL, set the
# JENGASEC_DB_* environment variables.
if os.environ.get("JENGASEC_DB_NAME"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["JENGASEC_DB_NAME"],
            "USER": os.environ.get("JENGASEC_DB_USER", "postgres"),
            "PASSWORD": os.environ.get("JENGASEC_DB_PASSWORD", ""),
            "HOST": os.environ.get("JENGASEC_DB_HOST", "localhost"),
            "PORT": os.environ.get("JENGASEC_DB_PORT", "5432"),
            "OPTIONS": {"sslmode": os.environ.get("JENGASEC_DB_SSLMODE", "prefer")},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, images)
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Hashed filenames + gzip/brotli, so static files can be cached forever and a
# deploy never serves a stale CSS file. Requires `collectstatic` at build time.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Uploads on Cloudflare R2 (S3-compatible) when the R2_* variables are set.
# Needed wherever the app's own disk is ephemeral (Render): otherwise every
# deploy deletes every submission. The bucket stays PRIVATE — file.url returns
# a signed link that expires, so submissions are never publicly listable.
#
#   R2_BUCKET             bucket name
#   R2_ENDPOINT_URL       https://<account-id>.r2.cloudflarestorage.com
#   R2_ACCESS_KEY_ID      from an R2 API token with Object Read & Write
#   R2_SECRET_ACCESS_KEY
if os.environ.get("R2_BUCKET"):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["R2_BUCKET"],
            "endpoint_url": os.environ["R2_ENDPOINT_URL"],
            "access_key": os.environ["R2_ACCESS_KEY_ID"],
            "secret_key": os.environ["R2_SECRET_ACCESS_KEY"],
            "region_name": "auto",           # R2 has one region: "auto"
            "signature_version": "s3v4",
            "default_acl": None,             # R2 has no ACLs; leave unset
            "file_overwrite": False,         # a same-named upload gets a suffix, never clobbers
            "querystring_auth": True,        # signed URLs...
            "querystring_expire": 3600,      # ...valid for one hour
        },
    }

# Uploaded submission documents (Django Media storage)
MEDIA_URL = "media/"
# Overridable so a host with an ephemeral filesystem (Render) can point it at a
# persistent disk — otherwise every deploy deletes every submission.
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Authentication flow
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:main_dashboard"
LOGOUT_REDIRECT_URL = "login"

# AI evaluation layer (Ollama)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")
