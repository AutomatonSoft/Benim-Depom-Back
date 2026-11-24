from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0033_product_warehouse_italy_poland")]

    operations = [
        migrations.AddField(
            model_name="productgeneratedimage",
            name="regen_status",
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
            model_name="productgeneratedimage",
            name="regen_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="productgeneratedimage",
            name="regen_claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
