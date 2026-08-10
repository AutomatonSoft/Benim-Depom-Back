from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from apps.accounts.models import User

from .models import Product
from .permissions import CanAccessProduct, IsSeller, is_manager
from .serializers import ProductSerializer

class ProductListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsSeller()]

        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = (
            Product.objects
            .select_related(
                "owner", 
                "product_type", 
                "category"
            )
            .prefetch_related(
                "variants__color", 
                "variants__material", 
                "images"
            )
        )

        user = self.request.user

        if not is_manager(user):
            queryset = queryset.filter(owner=user)

        status_value = self.request.query_params.get("status")
        if status_value:
            queryset = queryset.filter(status=status_value)


        return queryset.exclude(status=Product.Status.ARCHIVED)


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated, CanAccessProduct]

    def get_queryset(self):
        queryset = (
            Product.objects.select_related("owner", "product_type", "category")
            .prefetch_related(
                "variants__color",
                "variants__material",
                "images"
            )
        )

        if not is_manager(self.request.user):
            queryset = queryset.filter(owner=self.request.user)

        return queryset.exclude(status=Product.Status.ARCHIVED)

    def perform_destroy(self, instance):
        instance.status = Product.Status.ARCHIVED
        instance.save(update_fields=("status", "updated_at"))

        