from django.db import migrations


def release_eans_from_unapproved_products(apps, schema_editor):
    EanCode = apps.get_model("ean", "EanCode")
    EanCode.objects.filter(
        product__status__in=(
            "draft",
            "submitted",
            "under_review",
            "rejected",
        )
    ).update(product=None, assigned_at=None)


class Migration(migrations.Migration):
    dependencies = [
        ("ean", "0001_initial"),
        ("products", "0006_productvariant_color_hex_and_materials"),
    ]

    operations = [
        migrations.RunPython(
            release_eans_from_unapproved_products,
            migrations.RunPython.noop,
        ),
    ]
