# Generated after replacing the legacy database category catalog with the
# local OTTO JSON catalog.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0013_productimage_processing_claimed_at_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="product",
            name="category",
        ),
    ]
