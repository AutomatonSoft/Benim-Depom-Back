from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0018_remove_product_under_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="catalog_revision",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="product",
            name="pending_changes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="product",
            name="pending_changes_submitted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
