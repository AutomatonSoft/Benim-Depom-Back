from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsManager

from .catalog import get_categories, get_category_aspects


class EbayCategoriesQuerySerializer(serializers.Serializer):
    parent_id = serializers.RegexField(r"^[1-9][0-9]*$", max_length=20, required=False)


class EbayCategoryIdSerializer(serializers.Serializer):
    category_id = serializers.RegexField(r"^[1-9][0-9]*$", max_length=20)


class EbayCategorySerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    is_leaf = serializers.BooleanField()


class EbayCategoriesSerializer(serializers.Serializer):
    """Описывает один уровень дерева и его окружение для навигации менеджера."""

    environment = serializers.CharField()
    marketplace_id = serializers.CharField()
    category_tree_id = serializers.CharField()
    category_tree_version = serializers.CharField()
    parent = EbayCategorySerializer(allow_null=True)
    categories = EbayCategorySerializer(many=True)


class EbayCategoryAspectsSerializer(serializers.Serializer):
    """Сохраняет полную схему характеристик eBay и выделяет обязательные названия."""

    environment = serializers.CharField()
    marketplace_id = serializers.CharField()
    category_tree_id = serializers.CharField()
    category_tree_version = serializers.CharField()
    category = EbayCategorySerializer()
    aspects = serializers.ListField(child=serializers.DictField())
    required_aspects = serializers.ListField(child=serializers.CharField())


class EbayCategoriesView(APIView):
    """Позволяет менеджеру читать категории eBay Germany без изменения товаров."""

    permission_classes = (IsManager,)

    @extend_schema(
        parameters=[EbayCategoriesQuerySerializer],
        responses={200: EbayCategoriesSerializer},
        description=(
            "Без parent_id возвращает верхний уровень EBAY_DE; с parent_id — "
            "непосредственные дочерние категории. is_leaf=true обозначает конечную "
            "категорию. Использует токен приложения и текущее окружение eBay. "
            "Поиск getCategorySuggestions не вызывается: в Sandbox он не поддержан."
        ),
        tags=["eBay"],
    )
    def get(self, request):
        """Проверяет идентификатор родителя и выдаёт один уровень справочника."""
        query = EbayCategoriesQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        result = get_categories(**query.validated_data)
        return Response(EbayCategoriesSerializer(result).data)


class EbayCategoryAspectsView(APIView):
    """Показывает менеджеру требования к характеристикам конечной категории eBay."""

    permission_classes = (IsManager,)

    @extend_schema(
        responses={200: EbayCategoryAspectsSerializer},
        description=(
            "Возвращает aspects с исходными aspectConstraint, aspectValues и "
            "зависимостями eBay; required_aspects содержит названия обязательных "
            "характеристик. Неконечная категория возвращает 400, неизвестная — 404. "
            "Не сохраняет категорию или значения в товаре."
        ),
        tags=["eBay"],
    )
    def get(self, request, category_id):
        """Проверяет путь и читает характеристики выбранной конечной категории."""
        query = EbayCategoryIdSerializer(data={"category_id": category_id})
        query.is_valid(raise_exception=True)
        result = get_category_aspects(**query.validated_data)
        return Response(EbayCategoryAspectsSerializer(result).data)
