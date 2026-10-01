from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0026_productvariant_material_composition")]

    operations = [
        migrations.AddField(
            model_name="productimage",
            name="xl_cover_status",
            field=models.CharField(
                choices=[
                    ("idle", "Idle"),
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                ],
                default="idle",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="productimage",
            name="xl_cover_error",
            field=models.TextField(blank=True),
        ),
    ]
