"""Expose the current user's role names to every template as `user_roles`.

Usage in templates:
    {% if "blue_team" in user_roles %} ... {% endif %}
"""


def user_roles(request):
    if request.user.is_authenticated:
        return {"user_roles": set(request.user.groups.values_list("name", flat=True))}
    return {"user_roles": set()}
