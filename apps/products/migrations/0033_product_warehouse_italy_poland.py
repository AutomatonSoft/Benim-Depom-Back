from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0032_product_description")]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="warehouse_city",
            field=models.CharField(
                choices=[
                    ("IST", "Istanbul"),
                    ("ANK", "Ankara"),
                    ("IZM", "Izmir"),
                    ("BUR", "Bursa"),
                    ("KSY", "Kars"),
                    ("INE", "Inegol"),
                    ("ITA", "Italy"),
                    ("POL", "Poland"),
                ],
                default="INE",
                max_length=3,
            ),
        ),
    ]
