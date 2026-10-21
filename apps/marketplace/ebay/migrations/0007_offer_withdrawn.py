from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ebay", "0006_offer_publication")]

    operations = [
        migrations.AlterField(
            model_name="ebayofferpreparation",
            name="status",
            field=models.CharField(
                max_length=10,
                choices=[
                    ("processing", "Processing"),
                    ("prepared", "Prepared"),
                    ("published", "Published"),
                    ("withdrawn", "Withdrawn"),
                    ("failed", "Failed"),
                    ("uncertain", "Requires reconciliation"),
                ],
            ),
        ),
    ]
