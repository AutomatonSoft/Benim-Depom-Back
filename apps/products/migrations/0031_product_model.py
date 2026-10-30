from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0030_background_photo_uploads")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="model",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
