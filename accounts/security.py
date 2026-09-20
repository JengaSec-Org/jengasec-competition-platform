"""Login brute-force protection.

Django's LoginView will happily accept unlimited guesses. For an event
whose whole point is that people attack the platform's neighbours, an
unthrottled login is an obvious first stop.

Counts failures per (username, client IP) and refuses further attempts
once the threshold is hit, until the window expires.

NOTE: this uses Django's cache. The default locmem backend is per
process, so with more than one gunicorn worker the effective limit is
`MAX_ATTEMPTS x workers`. Point CACHES at Redis in production to make
the limit global.
"""
from django.conf import settings
from django.contrib.auth import views as auth_views
from django.core.cache import cache

MAX_ATTEMPTS = getattr(settings, "LOGIN_MAX_ATTEMPTS", 8)
LOCKOUT_SECONDS = getattr(settings, "LOGIN_LOCKOUT_SECONDS", 15 * 60)


def client_ip(request):
    """Best-effort client address.

    X-Forwarded-For is only trusted when the deployment sits behind the
    proxy configured in settings (SECURE_PROXY_SSL_HEADER); the left-most
    entry is the original client.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded and getattr(settings, "TRUST_FORWARDED_FOR", not settings.DEBUG):
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _key(username, ip):
    return f"login-fail:{(username or '').lower()[:150]}:{ip}"


def attempts(request, username):
    return cache.get(_key(username, client_ip(request)), 0)


def is_locked(request, username):
    return attempts(request, username) >= MAX_ATTEMPTS


def record_failure(request, username):
    key = _key(username, client_ip(request))
    try:
        count = cache.incr(key)
    except ValueError:  # key absent or expired
        cache.set(key, 1, LOCKOUT_SECONDS)
        count = 1
    return count


def clear(request, username):
    cache.delete(_key(username, client_ip(request)))


class ThrottledLoginView(auth_views.LoginView):
    """LoginView that stops answering after repeated failures.

    A locked pair is refused even when the password is correct, so the
    lockout cannot be used as an oracle to confirm a guess.
    """

    def form_valid(self, form):
        username = form.cleaned_data.get("username", "")
        if is_locked(self.request, username):
            return self.form_invalid(form)
        clear(self.request, username)
        return super().form_valid(form)

    def form_invalid(self, form):
        username = (form.data.get("username") or "").strip()
        if not is_locked(self.request, username):
            record_failure(self.request, username)
        if is_locked(self.request, username):
            form.errors.clear()
            form.add_error(
                None,
                "Too many failed sign-in attempts. Try again in "
                f"{LOCKOUT_SECONDS // 60} minutes.",
            )
        return super().form_invalid(form)
