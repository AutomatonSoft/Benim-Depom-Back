from django.db import migrations, models


def import_ebay_publications(apps, schema_editor):
    Preparation = apps.get_model("ebay", "EbayOfferPreparation")
    Publication = apps.get_model("orchestrator", "MarketplacePublication")
    database = schema_editor.connection.alias
    states = {
        "published": "active",
        "withdrawn": "deactivated",
        "prepared": "deactivated",
    }
    for preparation in (
        Preparation.objects.using(database)
        .filter(account="depotum")
        .exclude(offer_id="")
        .select_related("draft")
        .iterator()
    ):
        Publication.objects.using(database).get_or_create(
            product_id=preparation.draft.product_id,
            marketplace="ebay",
            account="depotum",
            environment=preparation.environment,
            defaults={
                "ean": preparation.sku,
                "status": states.get(preparation.status, "failed"),
                "external_id": preparation.listing_id,
                "external_reference": preparation.offer_id,
                "last_error": {"detail": preparation.last_error}
                if preparation.last_error
                else {},
                "published_at": preparation.updated_at
                if preparation.listing_id
                else None,
                "deactivated_at": preparation.updated_at
                if preparation.status == "withdrawn"
                else None,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("orchestrator", "0009_marketplacepublication_status_before_operation"),
        ("ebay", "0008_listing_id_history"),
    ]
    operations = [
        migrations.AddField(
            "marketplacepublication",
            "environment",
            models.CharField(max_length=10, blank=True, default=""),
        ),
        migrations.AlterField(
            "marketplacepublication",
            "marketplace",
            models.CharField(
                max_length=16,
                choices=[
                    ("otto", "OTTO"),
                    ("hood", "Hood"),
                    ("kaufland", "Kaufland"),
                    ("ebay", "eBay"),
                ],
            ),
        ),
        migrations.AlterField(
            "marketplacepublication",
            "account",
            models.CharField(
                max_length=7,
                choices=[("jv", "JV"), ("xl", "XL"), ("depotum", "Depotum")],
            ),
        ),
        migrations.RemoveConstraint(
            "marketplacepublication", "unique_product_marketplace_account"
        ),
        migrations.AddConstraint(
            "marketplacepublication",
            models.UniqueConstraint(
                fields=("product", "marketplace", "account", "environment"),
                name="unique_product_marketplace_account",
            ),
        ),
        migrations.RunPython(import_ebay_publications, migrations.RunPython.noop),
    ]
