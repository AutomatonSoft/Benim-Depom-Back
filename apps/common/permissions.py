from rest_framework.permissions import BasePermission

from apps.accounts.models import User


def is_manager(user) -> bool:
    return user.is_authenticated and (
        user.is_staff
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