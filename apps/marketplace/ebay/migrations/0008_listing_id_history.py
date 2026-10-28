from django.db import migrations, models


def seed_listing_ids(apps, schema_editor):
    """Сохраняет известный номер объявления для сопоставления продаж после перевыставления."""
    preparation = apps.get_model("ebay", "EbayOfferPreparation")
    for row in (
        preparation.objects.using(schema_editor.connection.alias)
        .exclude(listing_id="")
        .iterator()
    ):
        row.listing_id_history = [row.listing_id]
        row.save(
            using=schema_editor.connection.alias, update_fields=["listing_id_history"]
        )


class Migration(migrations.Migration):
    dependencies = [("ebay", "0007_offer_withdrawn")]

    operations = [
        migrations.AddField(
            model_name="ebayofferpreparation",
            name="listing_id_history",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.RunPython(seed_listing_ids, migrations.RunPython.noop),
    ]
