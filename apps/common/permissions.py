from rest_framework.permissions import BasePermission

from apps.accounts.models import User


def is_manager(user) -> bool:
    """
    API access is controlled by the business role.

    is_staff grants only access to Django Admin.
    is_superuser remains an emergency/developer override with full API access.
    """
    if not user or not user.is_authenticated:
        return False

    return (
        user.is_superuser
        or user.role in {
            User.Role.MANAGER,
            User.Role.ADMIN,
        }
    )


class IsManager(BasePermission):
    message = "Manager access is required."

    def has_permission(self, request, view):
        return is_manager(request.user)


class IsSeller(BasePermission):
    message = "Seller access is required."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == User.Role.SELLER
        )