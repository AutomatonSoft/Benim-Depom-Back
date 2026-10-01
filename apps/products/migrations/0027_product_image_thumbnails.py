import apps.products.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0026_productvariant_material_composition")]

    operations = [
        migrations.AddField(
            model_name="productimage",
            name="preview",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=apps.products.models.product_image_upload_to,
            ),
        ),
        migrations.AddField(
            model_name="productimage",
            name="thumbnail",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=apps.products.models.product_image_upload_to,
            ),
        ),
        migrations.AddField(
            model_name="productgeneratedimage",
            name="preview",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=apps.products.models.generated_image_upload_to,
            ),
        ),
        migrations.AddField(
            model_name="productgeneratedimage",
            name="thumbnail",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=apps.products.models.generated_image_upload_to,
            ),
        ),
    ]
