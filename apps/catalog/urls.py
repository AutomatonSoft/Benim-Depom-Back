from django.urls import path

from .views import (
    CategoryListView,
    ManagerCategoryDetailView,
    OttoCategoryGroupAttributesView,
    OttoCategoryGroupCategoriesView,
    OttoCategoryGroupListView,
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
    path("categories/", CategoryListView.as_view(), name="categories"),
    path(
        "categories/<int:pk>/",
        ManagerCategoryDetailView.as_view(),
        name="manager-category-detail",
    ),
]
