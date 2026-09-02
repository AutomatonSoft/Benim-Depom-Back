from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0008_notification_product_submitted_for_review"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="notification",
            name="data",
        ),
    ]
