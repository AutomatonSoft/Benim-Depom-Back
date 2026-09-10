from django.db import migrations, models


# One-time conversion of stored #RRGGBB values to the German family names
# that payloads previously derived at publish time.
_LEGACY_PALETTE = (
    (0, 0, 0, "Schwarz"),
    (255, 255, 255, "Weiß"),
    (48, 48, 48, "Anthrazit"),
    (128, 128, 128, "Grau"),
    (211, 211, 211, "Hellgrau"),
    (112, 128, 144, "Schiefergrau"),
    (192, 192, 192, "Silber"),
    (220, 20, 60, "Rot"),
    (139, 0, 0, "Dunkelrot"),
    (128, 0, 32, "Bordeaux"),
    (255, 192, 203, "Rosa"),
    (255, 140, 0, "Orange"),
    (255, 127, 80, "Koralle"),
    (198, 93, 59, "Terrakotta"),
    (255, 215, 0, "Gelb"),
    (225, 173, 1, "Senfgelb"),
    (212, 175, 55, "Gold"),
    (255, 253, 208, "Creme"),
    (34, 139, 34, "Grün"),
    (0, 100, 0, "Dunkelgrün"),
    (144, 238, 144, "Hellgrün"),
    (128, 128, 0, "Olivgrün"),
    (152, 255, 152, "Mintgrün"),
    (50, 205, 50, "Limettengrün"),
    (70, 130, 180, "Blau"),
    (0, 0, 139, "Dunkelblau"),
    (135, 206, 235, "Hellblau"),
    (0, 0, 128, "Marineblau"),
    (65, 105, 225, "Königsblau"),
    (30, 144, 255, "Azurblau"),
    (64, 224, 208, "Türkis"),
    (0, 95, 106, "Petrol"),
    (0, 255, 255, "Cyan"),
    (128, 0, 128, "Violett"),
    (106, 13, 173, "Lila"),
    (230, 230, 250, "Lavendel"),
    (142, 69, 133, "Pflaume"),
    (255, 0, 255, "Magenta"),
    (139, 69, 19, "Braun"),
    (78, 52, 46, "Dunkelbraun"),
    (198, 142, 90, "Hellbraun"),
    (139, 125, 107, "Taupe"),
    (245, 245, 220, "Beige"),
    (255, 218, 185, "Pfirsich"),
    (255, 255, 240, "Elfenbein"),
    (194, 178, 128, "Sand"),
    (175, 111, 9, "Karamell"),
    (184, 115, 51, "Kupfer"),
    (205, 127, 50, "Bronze"),
    (189, 183, 107, "Khaki"),
)


def _german_from_legacy_hex(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if len(normalized) != 7 or not normalized.startswith("#"):
        return str(value or "").strip()
    try:
        red = int(normalized[1:3], 16)
        green = int(normalized[3:5], 16)
        blue = int(normalized[5:7], 16)
    except ValueError:
        return str(value or "").strip()
    return min(
        _LEGACY_PALETTE,
        key=lambda color: (
            (red - color[0]) ** 2 + (green - color[1]) ** 2 + (blue - color[2]) ** 2
        ),
    )[3]


def convert_legacy_hex_colors(apps, schema_editor):
    ProductVariant = apps.get_model("products", "ProductVariant")
    for variant in ProductVariant.objects.iterator():
        converted = _german_from_legacy_hex(variant.color)
        if converted != variant.color:
            variant.color = converted
            variant.save(update_fields=("color",))


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0020_product_withdrawn_status"),
    ]

    operations = [
        migrations.RenameField(
            model_name="productvariant",
            old_name="color_hex",
            new_name="color",
        ),
        migrations.AlterField(
            model_name="productvariant",
            name="color",
            field=models.CharField(db_index=True, max_length=80),
        ),
        migrations.RunPython(convert_legacy_hex_colors, migrations.RunPython.noop),
    ]
