from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("products", "0014_remove_product_category"),
    ]

    operations = [
        migrations.DeleteModel(
            name="Category",
        ),
    ]
