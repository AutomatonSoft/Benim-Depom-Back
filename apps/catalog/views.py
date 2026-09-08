from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.otto_catalog import (
    SUPPORTED_OTTO_CATALOG_LANGUAGES,
    OttoCatalogError,
    UnsupportedOttoCatalogLanguage,
    get_otto_catalog,
    get_otto_catalog_translation,
)
from apps.catalog.otto_shipping_profiles import (
    OttoShippingProfilesError,
    get_otto_shipping_profiles,
)
from apps.common.permissions import IsManager

from .otto_serializers import (
    OttoCategoryAttributeSerializer,
    OttoCategoryGroupSerializer,
    OttoCategorySerializer,
    OttoShippingProfileSerializer,
)


class OttoCatalogPaginationMixin:
    """
    Pagination in RAM. The catalog itself is already cached as Python dicts.
    """

    default_limit = 50
    max_limit = 200

    def paginate_items(self, items):
        try:
            page = int(self.request.query_params.get("page", 1))
            limit = int(self.request.query_params.get("limit", self.default_limit))
        except (TypeError, ValueError):
            return None, Response(
                {"detail": "page and limit must be integers."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if page < 1 or limit < 1 or limit > self.max_limit:
            return None, Response(
                {
                    "detail": (
                        f"page must be at least 1; "
                        f"limit must be between 1 and {self.max_limit}."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        total = len(items)
        start = (page - 1) * limit
        end = start + limit

        return (
            {
                "count": total,
                "page": page,
                "limit": limit,
                "next": page + 1 if end < total else None,
                "previous": page - 1 if page > 1 else None,
                "results": items[start:end],
            },
            None,
        )


class OttoCatalogLocalizationMixin:
    """Loads the base catalog and one optional in-memory language overlay."""

    def get_catalog_and_translation(self, language: str):
        normalized_language = language.casefold()
        effective_language = (
            normalized_language
            if normalized_language in SUPPORTED_OTTO_CATALOG_LANGUAGES
            else "en"
        )

        try:
            return (
                get_otto_catalog(),
                get_otto_catalog_translation(effective_language),
                None,
            )
        except UnsupportedOttoCatalogLanguage:
            return (
                None,
                None,
                Response(
                    {
                        "detail": (
                            "Unsupported OTTO catalog language. "
                            "Available languages: de, en, tr, ru."
                        )
                    },
                    status=status.HTTP_404_NOT_FOUND,
                ),
            )
        except OttoCatalogError:
            return (
                None,
                None,
                Response(
                    {"detail": "OTTO catalog is temporarily unavailable."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                ),
            )

    @staticmethod
    def localize_group(group: dict, translation: dict | None) -> dict:
        localized_group = dict(group)
        if translation:
            group_id = str(group["category_group_id"])
            localized_group["category_group"] = translation["groups"].get(
                group_id,
                group["category_group"],
            )
        return localized_group

    @staticmethod
    def localize_category(category: dict, translation: dict | None) -> dict:
        localized_category = dict(category)
        if translation:
            category_id = str(category["categoryId"])
            group_id = str(category["category_group_id"])
            localized_category["name"] = translation["categories"].get(
                category_id,
                category["name"],
            )
            localized_category["category_group"] = translation["groups"].get(
                group_id,
                category["category_group"],
            )
        return localized_category

    @staticmethod
    def localize_attributes(
        group_id: int,
        attributes: list[dict],
        translation: dict | None,
    ) -> list[dict]:
        if not translation:
            return attributes

        translated_attributes = (
            translation["attribute_groups"]
            .get(
                str(group_id),
                {},
            )
            .get("attributes", {})
        )

        localized = []
        for attribute in attributes:
            translated = translated_attributes.get(str(attribute["attributeId"]), {})
            overlay = {
                field: translated[field]
                for field in ("name", "attributeGroup", "description", "unitDisplayName")
                if field in translated
            }
            labels = translated.get("allowedValues")
            if isinstance(labels, list):
                overlay["allowedValueLabels"] = labels
            localized.append({**attribute, **overlay})
        return localized


class OttoCategoryGroupListView(
    OttoCatalogLocalizationMixin,
    OttoCatalogPaginationMixin,
    APIView,
):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["OTTO - Catalog"],
        summary="List OTTO category groups",
        description=(
            "Returns OTTO category groups from the local JSON catalog. "
            "Without a suffix the response is German. Append /de/, /en/, /tr/, or "
            "/ru/; an unsupported suffix falls back to English."
        ),
        parameters=[
            OpenApiParameter(
                name="search",
                type=str,
                required=False,
                description="Part of a category group name.",
            ),
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="limit", type=int, required=False),
        ],
        responses={200: OttoCategoryGroupSerializer(many=True)},
    )
    def get(self, request, language: str = "de"):
        catalog, translation, error_response = self.get_catalog_and_translation(
            language
        )
        if error_response:
            return error_response

        search = request.query_params.get("search", "").strip().casefold()

        groups = [
            self.localize_group(
                {
                    "category_group_id": group_id,
                    "category_group": group["category_group"],
                    "category_count": len(
                        catalog["categories_by_group_id"].get(group_id, [])
                    ),
                },
                translation,
            )
            for group_id, group in catalog["groups_by_id"].items()
        ]

        if search:
            groups = [
                group
                for group in groups
                if search in group["category_group"].casefold()
            ]

        groups.sort(key=lambda group: group["category_group"].casefold())

        payload, error_response = self.paginate_items(groups)
        if error_response:
            return error_response

        payload["results"] = OttoCategoryGroupSerializer(
            payload["results"],
            many=True,
        ).data
        return Response(payload)


class OttoCategoryGroupCategoriesView(
    OttoCatalogLocalizationMixin,
    OttoCatalogPaginationMixin,
    APIView,
):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["OTTO - Catalog"],
        summary="List categories in an OTTO group",
        description=(
            "Returns selectable OTTO subcategories for one category group. "
            "Without a suffix the response is German. Append /de/, /en/, /tr/, or "
            "/ru/; an unsupported suffix falls back to English."
        ),
        parameters=[
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="limit", type=int, required=False),
        ],
        responses={200: OttoCategorySerializer(many=True)},
    )
    def get(self, request, group_id: int, language: str = "de"):
        catalog, translation, error_response = self.get_catalog_and_translation(
            language
        )
        if error_response:
            return error_response

        categories = catalog["categories_by_group_id"].get(group_id)
        if categories is None:
            return Response(
                {"detail": "Unknown OTTO category group ID."},
                status=status.HTTP_404_NOT_FOUND,
            )

        localized_categories = [
            self.localize_category(category, translation) for category in categories
        ]
        payload, error_response = self.paginate_items(localized_categories)
        if error_response:
            return error_response

        payload["results"] = OttoCategorySerializer(
            payload["results"],
            many=True,
        ).data
        return Response(payload)


class OttoCategoryGroupAttributesView(OttoCatalogLocalizationMixin, APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["OTTO - Catalog"],
        summary="Get attributes for an OTTO category group",
        description=(
            "Returns attribute definitions for the selected group. Without a suffix "
            "the response is German. Append /de/, /en/, /tr/, or /ru/; an "
            "unsupported suffix falls back to English. "
            "HIGH, MEDIUM and LOW relevance values are used only to order "
            "the manager/mobile UI; all attributes are optional."
        ),
        responses={200: OttoCategoryAttributeSerializer(many=True)},
    )
    def get(self, request, group_id: int, language: str = "de"):
        catalog, translation, error_response = self.get_catalog_and_translation(
            language
        )
        if error_response:
            return error_response

        attributes = catalog["attributes_by_group_id"].get(group_id)
        if attributes is None:
            return Response(
                {"detail": "Unknown OTTO category group ID."},
                status=status.HTTP_404_NOT_FOUND,
            )

        localized_attributes = self.localize_attributes(
            group_id,
            attributes,
            translation,
        )

        return Response(
            OttoCategoryAttributeSerializer(
                localized_attributes,
                many=True,
            ).data
        )


class OttoShippingProfileListView(APIView):
    permission_classes = [IsManager]

    @extend_schema(
        tags=["OTTO - Delivery"],
        summary="List OTTO shipping profiles for an account",
        description=(
            "Returns selectable shipping profile names for JV or XL. "
            "The frontend displays only shipping_profile_name and keeps "
            "shipping_profile_id as a hidden technical value."
        ),
        parameters=[
            OpenApiParameter(
                name="account",
                type=str,
                required=True,
                description="OTTO account: jv or xl.",
            ),
        ],
        responses={200: OttoShippingProfileSerializer(many=True)},
    )
    def get(self, request):
        account = request.query_params.get("account")

        if account not in {"jv", "xl"}:
            return Response(
                {"detail": "Query parameter account must be 'jv' or 'xl'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            catalog = get_otto_shipping_profiles()
        except OttoShippingProfilesError:
            return Response(
                {"detail": "OTTO shipping profiles are temporarily unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        profiles = catalog["profiles_by_account"][account]

        return Response(
            OttoShippingProfileSerializer(
                profiles,
                many=True,
            ).data
        )
