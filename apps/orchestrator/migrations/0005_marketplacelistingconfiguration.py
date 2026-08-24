from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0012_make_product_price_required"),
        ("orchestrator", "0004_alter_marketplacejob_operation"),
    ]

    operations = [
        migrations.CreateModel(
            name="MarketplaceListingConfiguration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "marketplace",
                    models.CharField(
                        choices=[
                            ("otto", "OTTO"),
                            ("hood", "Hood"),
                            ("kaufland", "Kaufland"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "account",
                    models.CharField(
                        choices=[("jv", "JV"), ("xl", "XL")],
                        max_length=2,
                    ),
                ),
                ("configuration", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="marketplace_listing_configurations",
                        to="products.product",
                    ),
                ),
            ],
            options={"ordering": ("marketplace", "account", "id")},
        ),
        migrations.AddConstraint(
            model_name="marketplacelistingconfiguration",
            constraint=models.UniqueConstraint(
                fields=("product", "marketplace", "account"),
                name="unique_product_marketplace_listing_config",
            ),
        ),
        migrations.AddIndex(
            model_name="marketplacelistingconfiguration",
            index=models.Index(
                fields=["product", "marketplace", "account"],
                name="market_cfg_product_channel_idx",
            ),
        ),
    ]
