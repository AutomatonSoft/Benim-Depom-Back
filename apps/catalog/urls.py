from django.urls import path

from .views import (
    OttoCategoryGroupAttributesView,
    OttoCategoryGroupCategoriesView,
    OttoCategoryGroupListView,
    OttoShippingProfileListView,
)

app_name = "catalog"


urlpatterns = [
    path(
        "otto/category-groups/",
        OttoCategoryGroupListView.as_view(),
        name="otto-category-group-list",
    ),
    path(
        "otto/category-groups/<int:group_id>/categories/",
        OttoCategoryGroupCategoriesView.as_view(),
        name="otto-category-group-categories",
    ),
    path(
        "otto/category-groups/<int:group_id>/attributes/",
        OttoCategoryGroupAttributesView.as_view(),
        name="otto-category-group-attributes",
    ),
    path(
        "otto/shipping-profiles/",
        OttoShippingProfileListView.as_view(),
        name="otto-shipping-profile-list",
    ),
]
