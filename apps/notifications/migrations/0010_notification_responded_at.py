from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0009_remove_notification_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="responded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
