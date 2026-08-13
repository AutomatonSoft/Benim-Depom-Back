from rest_framework import generics

from .models import Category

from rest_framework.permissions import AllowAny, IsAuthenticated

from apps.common.permissions import IsManager, is_manager

from .serializers import (
    CategorySerializer,
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



class ManagerCategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsManager]
