from django.db import migrations


def release_orphaned_reserved_eans(apps, schema_editor):
    EanCode = apps.get_model("ean", "EanCode")
    database = schema_editor.connection.alias
    EanCode.objects.using(database).filter(
        product__isnull=True,
        state="reserved",
    ).update(
        state="available",
        assigned_at=None,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("ean", "0004_eancode_ean_available_account_idx"),
    ]

    operations = [
        migrations.RunPython(
            release_orphaned_reserved_eans,
            migrations.RunPython.noop,
        ),
    ]
