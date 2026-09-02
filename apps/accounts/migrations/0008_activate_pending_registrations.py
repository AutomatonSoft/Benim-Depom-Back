from django.db import migrations


def activate_pending_verified_sellers(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(
        role="seller",
        is_email_verified=True,
        registration_status="pending",
    ).update(
        registration_status="approved",
        is_active=True,
        registration_rejection_reason="",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_user_registration_status"),
    ]

    operations = [
        migrations.RunPython(
            activate_pending_verified_sellers, migrations.RunPython.noop
        ),
    ]
