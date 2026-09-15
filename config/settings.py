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

# Uploaded submission documents (Django Media storage)
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

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

# AI evaluation layer (Ollama)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:4b") 