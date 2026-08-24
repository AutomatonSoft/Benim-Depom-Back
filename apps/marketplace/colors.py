"""Color normalization shared by all marketplace payload builders.

The mobile client sends the exact shade as ``#RRGGBB``.  Marketplaces need a
human-readable value, so we preserve the exact HEX on the product and map it
only while building an external payload.  The palette is deliberately small:
marketplace catalogues normally accept or expect basic German color families,
not one name for each of the millions of possible HEX values.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NamedColor:
    red: int
    green: int
    blue: int
    german_name: str


# Basic e-commerce color families.  These are intentionally product-friendly
# labels in German, rather than artistic names for every individual shade.
# Add a new family here only when a marketplace explicitly requires it; no
# database migration is needed.
COLOR_PALETTE: tuple[NamedColor, ...] = (
    NamedColor(0, 0, 0, "Schwarz"),
    NamedColor(255, 255, 255, "Weiß"),
    NamedColor(48, 48, 48, "Anthrazit"),
    NamedColor(128, 128, 128, "Grau"),
    NamedColor(211, 211, 211, "Hellgrau"),
    NamedColor(112, 128, 144, "Schiefergrau"),
    NamedColor(192, 192, 192, "Silber"),
    NamedColor(220, 20, 60, "Rot"),
    NamedColor(139, 0, 0, "Dunkelrot"),
    NamedColor(128, 0, 32, "Bordeaux"),
    NamedColor(255, 192, 203, "Rosa"),
    NamedColor(255, 140, 0, "Orange"),
    NamedColor(255, 127, 80, "Koralle"),
    NamedColor(198, 93, 59, "Terrakotta"),
    NamedColor(255, 215, 0, "Gelb"),
    NamedColor(225, 173, 1, "Senfgelb"),
    NamedColor(212, 175, 55, "Gold"),
    NamedColor(255, 253, 208, "Creme"),
    NamedColor(34, 139, 34, "Grün"),
    NamedColor(0, 100, 0, "Dunkelgrün"),
    NamedColor(144, 238, 144, "Hellgrün"),
    NamedColor(128, 128, 0, "Olivgrün"),
    NamedColor(152, 255, 152, "Mintgrün"),
    NamedColor(50, 205, 50, "Limettengrün"),
    NamedColor(70, 130, 180, "Blau"),
    NamedColor(0, 0, 139, "Dunkelblau"),
    NamedColor(135, 206, 235, "Hellblau"),
    NamedColor(0, 0, 128, "Marineblau"),
    NamedColor(65, 105, 225, "Königsblau"),
    NamedColor(30, 144, 255, "Azurblau"),
    NamedColor(64, 224, 208, "Türkis"),
    NamedColor(0, 95, 106, "Petrol"),
    NamedColor(0, 255, 255, "Cyan"),
    NamedColor(128, 0, 128, "Violett"),
    NamedColor(106, 13, 173, "Lila"),
    NamedColor(230, 230, 250, "Lavendel"),
    NamedColor(142, 69, 133, "Pflaume"),
    NamedColor(255, 0, 255, "Magenta"),
    NamedColor(139, 69, 19, "Braun"),
    NamedColor(78, 52, 46, "Dunkelbraun"),
    NamedColor(198, 142, 90, "Hellbraun"),
    NamedColor(139, 125, 107, "Taupe"),
    NamedColor(245, 245, 220, "Beige"),
    NamedColor(255, 218, 185, "Pfirsich"),
    NamedColor(255, 255, 240, "Elfenbein"),
    NamedColor(194, 178, 128, "Sand"),
    NamedColor(175, 111, 9, "Karamell"),
    NamedColor(184, 115, 51, "Kupfer"),
    NamedColor(205, 127, 50, "Bronze"),
    NamedColor(189, 183, 107, "Khaki"),
)


def german_color_name(hex_color: str) -> str:
    """Return the closest basic German color name for a ``#RRGGBB`` value.

    Product serializers already validate HEX values.  This defensive check
    still makes direct service usage fail clearly rather than sending malformed
    marketplace data.
    """

    normalized = str(hex_color).strip().upper()

    if len(normalized) != 7 or not normalized.startswith("#"):
        raise ValueError("Color must use the #RRGGBB format.")

    try:
        red = int(normalized[1:3], 16)
        green = int(normalized[3:5], 16)
        blue = int(normalized[5:7], 16)
    except ValueError as exc:
        raise ValueError("Color must use the #RRGGBB format.") from exc

    return min(
        COLOR_PALETTE,
        key=lambda color: (
            (red - color.red) ** 2
            + (green - color.green) ** 2
            + (blue - color.blue) ** 2
        ),
    ).german_name
