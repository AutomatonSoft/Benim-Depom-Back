from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.accounts.models import User

from .models import Product


def is_manager(user) -> bool:
    return user.is_staff or user.role in {
        User.Role.MANAGER,
        User.Role.ADMIN,
    }


class IsSeller(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == User.Role.SELLER


class CanAccessProduct(BasePermission):
    def has_object_permission(self, request, view, product):
        if is_manager(request.user):
            return True

        if product.owner_id != request.user.id:
            return False

        if request.method in SAFE_METHODS:
            return True

        return product.status in {
            Product.Status.DRAFT,
            Product.Status.REJECTED,
        }