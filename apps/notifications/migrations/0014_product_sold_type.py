from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0013_notification_price_negotiation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="notification_type",
            field=models.CharField(
                choices=[
                    ("product_approved", "Product approved"),
                    ("product_rejected", "Product rejected"),
                    ("product_confirmation", "Product confirmation"),
                    ("image_processing_completed", "Image processing completed"),
                    ("image_processing_failed", "Image processing failed"),
                    ("message_received", "Message received"),
                    ("product_availability_reminder", "Product availability reminder"),
                    ("manager_message", "Manager message"),
                    ("product_deactivated", "Product deactivated"),
                    (
                        "product_deactivation_requested",
                        "Product deactivation requested",
                    ),
                    ("product_submitted_for_review", "Product submitted for review"),
                    (
                        "product_withdrawn_from_review",
                        "Product withdrawn from review",
                    ),
                    ("product_change_requested", "Product change requested"),
                    ("price_negotiation_offer", "Price negotiation offer"),
                    ("price_negotiation_response", "Price negotiation response"),
                    ("product_sold", "Product sold"),
                ],
                max_length=50,
            ),
        ),
    ]
