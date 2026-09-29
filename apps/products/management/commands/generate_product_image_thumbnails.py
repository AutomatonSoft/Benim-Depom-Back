from contextlib import nullcontext

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.products.image_optimization import make_product_image_variants
from apps.products.models import ProductGeneratedImage, ProductImage


class Command(BaseCommand):
    help = "Create missing small JPG thumbnails for product images."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be a positive integer.")
        images = ProductImage.objects.filter(
            Q(thumbnail__isnull=True) | Q(thumbnail=""),
        ).select_related("product")
        generated_images = ProductGeneratedImage.objects.filter(
            Q(thumbnail__isnull=True) | Q(thumbnail=""),
        ).select_related("source_image__product")

        image_count = self._create_thumbnails(images, limit)
        remaining = None if limit is None else max(limit - image_count, 0)
        generated_count = self._create_thumbnails(generated_images, remaining)
        self.stdout.write(
            self.style.SUCCESS(
                "Created optimized previews and thumbnails for "
                f"{image_count + generated_count} product images."
            )
        )

    def _create_thumbnails(self, queryset, limit):
        if limit == 0:
            return 0
        if limit is not None:
            queryset = queryset[:limit]

        count = 0
        for instance in queryset.iterator(chunk_size=50):
            image = instance.image
            if not image:
                continue
            reuse = getattr(image.storage, "reuse_connection", None)
            with reuse() if reuse else nullcontext():
                with image.open("rb") as source:
                    preview, thumbnail = make_product_image_variants(source)
                    if not instance.preview:
                        instance.preview.save(preview.name, preview, save=False)
                    if not instance.thumbnail:
                        instance.thumbnail.save(thumbnail.name, thumbnail, save=False)
                    instance.save(update_fields=("preview", "thumbnail"))
            count += 1
        return count
