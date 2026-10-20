from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ebay", "0005_ebaydraft_regulatory_ebayofferpreparation")]

    operations = [
        migrations.AddField(
            model_name="ebayofferpreparation",
            name="listing_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AlterField(
            model_name="ebayofferpreparation",
            name="status",
            field=models.CharField(
                max_length=10,
                choices=[
                    ("processing", "Processing"),
                    ("prepared", "Prepared"),
                    ("published", "Published"),
                    ("failed", "Failed"),
                    ("uncertain", "Requires reconciliation"),
                ],
            ),
        ),
    ]
