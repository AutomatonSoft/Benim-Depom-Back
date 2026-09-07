from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.common.permissions import is_manager

from .models import Product


class CanAccessProduct(BasePermission):
    def has_object_permission(self, request, view, product):
        if is_manager(request.user):
            return True

        if product.owner_id != request.user.id:
            return False

        if request.method in SAFE_METHODS:
            return True

        if request.method == "DELETE":
            return product.status in {
                Product.Status.DRAFT,
                Product.Status.REJECTED,
                Product.Status.WITHDRAWN,
            }

        return product.status in {
            Product.Status.DRAFT,
            Product.Status.SUBMITTED,
            Product.Status.REJECTED,
            Product.Status.WITHDRAWN,
            Product.Status.APPROVED,
        }
