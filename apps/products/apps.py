from django.apps import AppConfig


class ProductsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.products"
    label = "products"

    def ready(self):
        from django.conf import settings
        from PIL import Image

        # Keep Pillow's decompression-bomb guard aligned with product validation.
        Image.MAX_IMAGE_PIXELS = settings.PRODUCT_IMAGE_MAX_PIXELS
