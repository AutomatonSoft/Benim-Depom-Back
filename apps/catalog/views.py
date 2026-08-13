from rest_framework import generics

from .models import Category, ProductType

from rest_framework.permissions import AllowAny, IsAuthenticated

from apps.common.permissions import IsManager, is_manager

from .serializers import (
    CategorySerializer,
    ProductTypeSerializer
)



    
class CategoryListView(generics.ListCreateAPIView):
    serializer_class = CategorySerializer

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsManager()]
        return [AllowAny()]

    def get_queryset(self):
        queryset = Category.objects.all()

        if not is_manager(self.request.user):
            queryset = queryset.filter(is_active=True)

        return queryset



class ProductTypeListView(generics.ListAPIView):
    queryset = ProductType.objects.filter(is_active=True)
    serializer_class = ProductTypeSerializer
    permission_classes = [IsAuthenticated]



class ManagerCategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsManager]


class ManagerProductTypeDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = ProductType.objects.all()
    serializer_class = ProductTypeSerializer
    permission_classes = [IsManager]
