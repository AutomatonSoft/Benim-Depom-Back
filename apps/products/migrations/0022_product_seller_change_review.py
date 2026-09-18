from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0021_productvariant_color_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="seller_change_review",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
