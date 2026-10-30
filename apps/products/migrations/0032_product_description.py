from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0031_product_model")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="description",
            field=models.TextField(blank=True, default="", max_length=1200),
        ),
    ]
