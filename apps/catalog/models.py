from django.db import models


class BaseCatalogModel(models.Model):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ("sort_order", "name")

    def __str__(self) -> str:
        return self.name


class Category(BaseCatalogModel):
    pass


class ProductType(BaseCatalogModel):
    pass 


class Material(BaseCatalogModel):
    pass 


class Color(BaseCatalogModel):
    hex_code = models.CharField(max_length=7, blank=True)

