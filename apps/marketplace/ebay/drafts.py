from urllib.parse import quote

from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session
from apps.common.permissions import IsManager
from apps.common.throttles import ManagerMutationThrottleMixin
from apps.products.models import Product

from .catalog import get_category_aspects
from .client import api_base_url, get_seller_resource
from .models import EbayDraft
from .readiness import _location_summary, _policy_summary
from .services import get_access_token

POLICY_FIELDS = {
    "fulfillment_policy_id": ("fulfillment", "fulfillmentPolicyId"),
    "payment_policy_id": ("payment", "paymentPolicyId"),
    "return_policy_id": ("return", "returnPolicyId"),
}
LISTING_SETTINGS_FIELDS = (*POLICY_FIELDS, "merchant_location_key")


def _validate_listing_settings(*, account, values):
    """Проверяет ссылки чтением API текущего продавца; политики и склад не изменяет."""
    if not values:
        return
    access_token = get_access_token(account=account)
    errors = {}
    with build_marketplace_session() as session:
        for adapter in session.adapters.values():
            adapter.max_retries = Retry(total=0)
        for field, value in values.items():
            if field == "merchant_location_key":
                payload = get_seller_resource(
                    session=session,
                    access_token=access_token,
                    path=f"/sell/inventory/v1/location/{quote(value, safe='')}",
                )
                location = _location_summary(payload)
                if (
                    location["merchant_location_key"] != value
                    or not location["usable"]
                    or not isinstance(location["location_types"], list)
                    or "WAREHOUSE" not in location["location_types"]
                ):
                    errors[field] = "Choose an enabled warehouse with a usable address."
            else:
                resource, id_field = POLICY_FIELDS[field]
                payload = get_seller_resource(
                    session=session,
                    access_token=access_token,
                    path=f"/sell/account/v1/{resource}_policy/{value}",
                )
                policy = _policy_summary(payload, id_field)
                if policy["id"] != value or not policy["applicable_to_furniture"]:
                    errors[field] = (
                        "Choose this seller's EBAY_DE policy for non-motor items."
                    )
    if errors:
        raise serializers.ValidationError(errors)


def _validate_aspects(aspects, schema):
    """Проверяет известные имена, число, длину и разрешённые значения характеристик."""
    definitions = {item["localizedAspectName"]: item for item in schema["aspects"]}
    errors = {}
    for name, values in aspects.items():
        definition = definitions.get(name)
        if definition is None:
            errors[name] = "Unknown aspect for this category."
            continue
        constraint = definition["aspectConstraint"]
        if (
            constraint.get("itemToAspectCardinality", "SINGLE") == "SINGLE"
            and len(values) > 1
        ):
            errors[name] = "Only one value is allowed."
        elif constraint.get("aspectMaxLength") and any(
            len(value) > constraint["aspectMaxLength"] for value in values
        ):
            errors[name] = "Aspect value is too long."
        elif constraint.get("aspectMode") == "SELECTION_ONLY":
            allowed = {
                value["localizedValue"] for value in definition.get("aspectValues", [])
            }
            if any(value not in allowed for value in values):
                errors[name] = "Choose values from the category's allowed list."
    if errors:
        raise serializers.ValidationError({"aspects": errors})


class EbayDraftSerializer(serializers.ModelSerializer):
    """Проверяет введённые поля и допускает сохранение неполного черновика JV."""

    category_id = serializers.RegexField(
        r"^[1-9][0-9]*$", max_length=20, allow_blank=True, required=False
    )
    image_urls = serializers.ListField(
        child=serializers.URLField(max_length=2048), max_length=24, required=False
    )
    aspects = serializers.DictField(
        child=serializers.ListField(
            child=serializers.CharField(max_length=1024),
            allow_empty=False,
            max_length=30,
        ),
        required=False,
    )
    fulfillment_policy_id = serializers.RegexField(
        r"^[1-9][0-9]*$", max_length=64, allow_blank=True, required=False
    )
    payment_policy_id = serializers.RegexField(
        r"^[1-9][0-9]*$", max_length=64, allow_blank=True, required=False
    )
    return_policy_id = serializers.RegexField(
        r"^[1-9][0-9]*$", max_length=64, allow_blank=True, required=False
    )
    currency = serializers.CharField(read_only=True, default="EUR")
    marketplace_id = serializers.CharField(read_only=True, default="EBAY_DE")
    missing_requirements = serializers.SerializerMethodField()

    class Meta:
        model = EbayDraft
        fields = (
            "id",
            "product",
            "environment",
            "account",
            "marketplace_id",
            "currency",
            "title",
            "description",
            "image_urls",
            "price_eur",
            "quantity",
            "category_id",
            "aspects",
            "category_tree_id",
            "category_tree_version",
            "required_aspects",
            "fulfillment_policy_id",
            "payment_policy_id",
            "return_policy_id",
            "merchant_location_key",
            "missing_requirements",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "product",
            "environment",
            "account",
            "category_tree_id",
            "category_tree_version",
            "required_aspects",
            "created_at",
            "updated_at",
        )
        # Уникальность задают серверные product/environment/account и ограничение БД.
        validators = []

    def validate_image_urls(self, values):
        """Сохраняет порядок фото и принимает только HTTPS-ссылки без загрузки файлов."""
        if any(not value.lower().startswith("https://") for value in values):
            raise serializers.ValidationError("Image URLs must use HTTPS.")
        return values

    def validate_merchant_location_key(self, value):
        """Не допускает специальные сегменты пути в ключе склада для запроса eBay."""
        if value in {".", ".."}:
            raise serializers.ValidationError("Invalid merchant location key.")
        return value

    def validate(self, attrs):
        """Проверяет изменённые ссылки eBay, категорию и характеристики черновика."""
        changed_settings = {
            field: attrs[field]
            for field in LISTING_SETTINGS_FIELDS
            if attrs.get(field) and attrs[field] != getattr(self.instance, field, "")
        }
        _validate_listing_settings(
            account=getattr(self.instance, "account", "jv"), values=changed_settings
        )
        if not ({"category_id", "aspects"} & attrs.keys()):
            return attrs
        category_id = attrs.get(
            "category_id", getattr(self.instance, "category_id", "")
        )
        previous_aspects = getattr(self.instance, "aspects", {})
        if (
            self.instance
            and category_id != self.instance.category_id
            and previous_aspects
            and "aspects" not in attrs
        ):
            raise serializers.ValidationError(
                {
                    "aspects": "Provide aspects when changing category; use {} to clear them."
                }
            )
        aspects = attrs.get("aspects", previous_aspects)
        if not category_id:
            if aspects:
                raise serializers.ValidationError(
                    {"category_id": "Choose a category before entering aspects."}
                )
            attrs.update(
                category_tree_id="", category_tree_version="", required_aspects=[]
            )
            return attrs
        try:
            schema = get_category_aspects(category_id=category_id)
        except NotFound as error:
            raise serializers.ValidationError(
                {"category_id": "Unknown eBay category."}
            ) from error
        _validate_aspects(aspects, schema)
        attrs.update(
            category_tree_id=schema["category_tree_id"],
            category_tree_version=schema["category_tree_version"],
            required_aspects=schema["required_aspects"],
        )
        return attrs

    @extend_schema_field(
        serializers.DictField(
            child=serializers.ListField(child=serializers.CharField())
        )
    )
    def get_missing_requirements(self, obj):
        """Показывает незаполненные поля по сохранённой схеме, не обращаясь к eBay."""
        return {
            "fields": [
                field
                for field in (
                    "title",
                    "description",
                    "image_urls",
                    "price_eur",
                    "quantity",
                    "category_id",
                    *LISTING_SETTINGS_FIELDS,
                )
                if getattr(obj, field) in ("", None, [])
            ],
            "aspects": [
                name for name in obj.required_aspects if not obj.aspects.get(name)
            ],
        }


class EbayDraftView(ManagerMutationThrottleMixin, APIView):
    """Даёт менеджеру читать и сохранять локальный черновик JV без публикации."""

    permission_classes = (IsManager,)

    def _scope(self, product_id):
        """Привязывает запрос к существующему товару, JV и окружению сервера."""
        api_base_url()
        product = get_object_or_404(Product.objects.only("pk"), pk=product_id)
        return {
            "product": product,
            "environment": settings.EBAY_ENVIRONMENT,
            "account": "jv",
        }

    @extend_schema(responses={200: EbayDraftSerializer}, tags=["eBay"])
    def get(self, request, product_id):
        """Возвращает сохранённый черновик или 404, не создавая запись при чтении."""
        draft = get_object_or_404(EbayDraft, **self._scope(product_id))
        return Response(EbayDraftSerializer(draft).data)

    @transaction.atomic
    def _save(self, request, product_id, *, partial):
        """Сохраняет только поля черновика; уникальность и конкурентное создание контролирует БД."""
        scope = self._scope(product_id)
        draft = EbayDraft.objects.select_for_update().filter(**scope).first()
        if partial and draft is None:
            raise NotFound("Create the eBay draft with PUT first.")
        serializer = EbayDraftSerializer(draft, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        created = draft is None
        if created:
            draft = EbayDraft(**scope, **serializer.validated_data)
            try:
                draft.save(force_insert=True)
            except IntegrityError as error:
                raise serializers.ValidationError(
                    "Draft was created concurrently. Retry the request."
                ) from error
        else:
            for name, value in serializer.validated_data.items():
                setattr(draft, name, value)
            draft.save(update_fields=[*serializer.validated_data, "updated_at"])
        return Response(EbayDraftSerializer(draft).data, status=201 if created else 200)

    @extend_schema(
        request=EbayDraftSerializer,
        responses={200: EbayDraftSerializer, 201: EbayDraftSerializer},
        tags=["eBay"],
        description="Создаёт или обновляет локальный черновик JV. Незаполненные обязательные поля перечислены в missing_requirements; публикация не выполняется.",
    )
    def put(self, request, product_id):
        """Создаёт черновик или обновляет переданные поля существующего."""
        return self._save(request, product_id, partial=False)

    @extend_schema(
        request=EbayDraftSerializer, responses={200: EbayDraftSerializer}, tags=["eBay"]
    )
    def patch(self, request, product_id):
        """Изменяет переданные поля ранее сохранённого черновика."""
        return self._save(request, product_id, partial=True)
