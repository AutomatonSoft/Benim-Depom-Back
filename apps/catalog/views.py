from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .models import Category, Color, Material, ProductType

from .serializers import (
    CategorySerializer,
    ColorSerializer,
    MaterialSerializer,
    ProductTypeSerializer
)


class CategoryListView(generics.ListAPIView):
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]



class ProductTypeListView(generics.ListAPIView):
    queryset = ProductType.objects.filter(is_active=True)
    serializer_class = ProductTypeSerializer
    permission_classes = [IsAuthenticated]



class MaterialListView(generics.ListAPIView):
    queryset = Material.objects.filter(is_active=True)
    serializer_class = MaterialSerializer
    permission_classes = [IsAuthenticated]



class ColorListView(generics.ListAPIView):
    queryset = Color.objects.filter(is_active=True)
    serializer_class = ColorSerializer
    permission_classes = [IsAuthenticated]




