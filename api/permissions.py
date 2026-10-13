from rest_framework.permissions import BasePermission


class IsJudgeOrStaff(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        if not u.is_authenticated:
            return False
        return u.is_staff or getattr(getattr(u, "profile", None), "role", "") == "judge"