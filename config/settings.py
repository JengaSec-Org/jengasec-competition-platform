"""
Django settings for the JengaSec Evaluation Platform.

Architecture: Browser -> Templates -> Views -> Services -> Models -> PostgreSQL
Business logic lives in `services/`, not in views.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Local development convenience: read BASE_DIR/.env if python-dotenv is
# installed. In production the environment is supplied by systemd from a
# root-owned EnvironmentFile, and no .env exists. `.env` is gitignored --
# the Gmail app password must never reach the repository.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover - optional dependency
    pass

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
    "audit",
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
                "accounts.context_processors.competition_phase",
                "notifications.context_processors.unread_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# Default: SQLite so anyone can run the project immediately.
# For the shared test DB (Neon) or production PostgreSQL, EITHER set
# JENGASEC_DB_URL to the whole connection string a provider hands you —
#   postgresql://user:password@host/dbname?sslmode=require
# — OR set the individual JENGASEC_DB_* variables. The URL wins if both exist.
if os.environ.get("JENGASEC_DB_URL"):
    from urllib.parse import parse_qs, unquote, urlparse

    _db = urlparse(os.environ["JENGASEC_DB_URL"])
    _db_query = {k: v[0] for k, v in parse_qs(_db.query).items()}
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _db.path.lstrip("/"),
            "USER": unquote(_db.username or ""),
            "PASSWORD": unquote(_db.password or ""),
            "HOST": _db.hostname or "localhost",
            "PORT": str(_db.port or 5432),
            # Hosted Postgres is always reached over the internet: require TLS
            # unless the URL itself says otherwise.
            "OPTIONS": {"sslmode": _db_query.get("sslmode", "require")},
        }
    }
elif os.environ.get("JENGASEC_DB_NAME"):
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

# Uploads on any S3-compatible object store when the S3_* variables are set.
# Needed wherever the app's own disk is ephemeral (Render): otherwise every
# deploy deletes every submission. The bucket stays PRIVATE — file.url returns
# a signed link that expires, so submissions are never publicly listable.
#
#   S3_BUCKET             bucket name
#   S3_ENDPOINT_URL       Backblaze B2:   https://s3.<region>.backblazeb2.com
#                         Cloudflare R2:  https://<account-id>.r2.cloudflarestorage.com
#                         Supabase:       https://<project>.supabase.co/storage/v1/s3
#   S3_REGION             B2: the region in the endpoint (e.g. eu-central-003)
#                         R2: auto        Supabase: the project's region
#   S3_ACCESS_KEY_ID      an application key / API token with read+write on the bucket
#   S3_SECRET_ACCESS_KEY
if os.environ.get("S3_BUCKET"):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["S3_BUCKET"],
            "endpoint_url": os.environ["S3_ENDPOINT_URL"],
            "access_key": os.environ["S3_ACCESS_KEY_ID"],
            "secret_key": os.environ["S3_SECRET_ACCESS_KEY"],
            "region_name": os.environ.get("S3_REGION", "auto"),
            "signature_version": "s3v4",
            "default_acl": None,             # private bucket; R2/B2 ignore ACLs anyway
            "file_overwrite": False,         # a same-named upload gets a suffix, never clobbers
            "querystring_auth": True,        # signed URLs...
            "querystring_expire": 3600,      # ...valid for one hour
        },
    }

# Uploaded submission documents (Django Media storage)
# Uploaded submission documents (Django Media storage).
# MEDIA is never served as static files — see config/urls.py.
MEDIA_URL = "media/"
# Overridable so a host with an ephemeral filesystem (Render) can point it at a
# persistent disk — otherwise every deploy deletes every submission.
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))

# Upload limits. Django streams anything over FILE_UPLOAD_MAX_MEMORY_SIZE
# to a temp file, and only then runs form validation — so without a cap
# a huge POST fills the disk before the 50 MB form check ever runs.
# The views also refuse on Content-Length before reading the body.
#
# nginx must set `client_max_body_size 55m;` to reject earlier still.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024  # non-file POST data
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # spool to disk beyond this
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

X_FRAME_OPTIONS = "SAMEORIGIN"

# Authentication flow
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:main_dashboard"
LOGOUT_REDIRECT_URL = "login"

# Email — console backend in dev (password reset emails print to the
# runserver terminal). Swap for SMTP settings in production.
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = "JengaSec <sucybersec@strathmore.edu>"
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# SMTP, used once DJANGO_EMAIL_BACKEND selects the SMTP backend. Gmail with
# an app password: the account needs 2-Step Verification enabled, and the
# Workspace administrator must not have disabled app passwords for the
# domain.
#
# EMAIL_HOST_USER has to match the address in DEFAULT_FROM_EMAIL, or Gmail
# rewrites the From header and the mail reads as spoofed.
#
# The password comes from the environment and nowhere else. Production
# supplies it from ansible-vault via a mode-0600 EnvironmentFile; it is
# never a default here, so a missing secret fails loudly instead of
# silently sending as somebody else.
EMAIL_HOST = os.environ.get("DJANGO_EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("DJANGO_EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("DJANGO_EMAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
EMAIL_HOST_USER = os.environ.get("DJANGO_EMAIL_HOST_USER", "sucybersec@strathmore.edu")
EMAIL_HOST_PASSWORD = os.environ.get("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = int(os.environ.get("DJANGO_EMAIL_TIMEOUT", "10"))

# Absolute base for links inside notification emails (relative paths are
# meaningless in a mail client).
SITE_URL = os.environ.get("DJANGO_SITE_URL", "http://localhost:8000")

# AI evaluation layer (Ollama)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:4b")

# ─────────────────────────────────────────────────────────────────────────────
# Running behind a reverse proxy
#
# In production nginx terminates TLS and forwards a plain HTTP request to
# gunicorn. Django therefore sees http:// and, without the settings below,
# concludes every request is insecure. Everything here is environment-driven,
# so development behaviour is unchanged.
# ─────────────────────────────────────────────────────────────────────────────

# How Django learns the original request was HTTPS. nginx sets this header;
# request.is_secure() is False without it, which breaks secure cookies and the
# CSRF origin check below.
#
# Only safe because nginx always OVERWRITES this header rather than passing a
# client-supplied one through — otherwise anyone could claim to be on HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Django 4+ rejects any POST whose Origin is not listed here. Behind TLS that
# means EVERY login fails, with a CSRF error that does not mention the cause.
# Entries must include the scheme: https://platform.jengasec.local
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

if not DEBUG:
    # Cookies only over HTTPS. Requires SECURE_PROXY_SSL_HEADER above to be
    # correct, or Django decides the connection is insecure and never sets them.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"

    # nginx sets these headers too. Django only sends them on responses it
    # generates, so they are belt and braces rather than the primary control.
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "SAMEORIGIN"

    # SECURE_SSL_REDIRECT is deliberately NOT set. nginx already redirects
    # HTTP to HTTPS, and enabling both produces a redirect loop whenever the
    # proxy header is misconfigured — a failure that is hard to diagnose
    # because the browser only reports "too many redirects".

    # HSTS is set by nginx, which owns one policy in one place. Left at 0 here
    # so the two cannot disagree. `manage.py check --deploy` warns about this;
    # the warning is expected.
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))
