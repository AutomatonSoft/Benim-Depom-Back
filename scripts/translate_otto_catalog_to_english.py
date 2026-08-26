"""Create and collect a one-off OpenAI Batch English translation of the OTTO catalog.

The commands and validation are shared with the Turkish catalog translator.
This version writes English overlays to ``data/otto/translations/en``.
"""

from __future__ import annotations

import translate_otto_catalog_to_turkish as translation

translation.__doc__ = __doc__
translation.DEFAULT_OUTPUT_DIR = translation.DEFAULT_SOURCE_DIR / "translations" / "en"
translation.DEFAULT_WORK_DIR = translation.ROOT_DIR / ".translation-work" / "otto-en"
translation.LANGUAGE = "en"
translation.LANGUAGE_NAME = "English"
translation.BATCH_PREFIX = "otto-en"
translation.TRANSLATION_SCHEMA_NAME = "otto_english_catalog_translations"
translation.RETRY_SCHEMA_NAME = "otto_english_catalog_retry"
translation.TRANSLATION_PURPOSE = "otto_catalog_en_translation"
translation.INSTRUCTIONS = """
Translate German OTTO marketplace catalog labels to clear, natural English.

Rules:
- Translate only the supplied `text` field and return every supplied `key` once.
- Keep each `key` byte-for-byte unchanged.
- Preserve numbers, model codes, dimensions, units, brand names, and technical
  identifiers when they must not be translated.
- Do not add facts, explanations, markdown, quotes, or labels.
- For product attributes, use concise terminology that an English-speaking
  seller can understand in a product form.
- Return only JSON matching the supplied schema.
""".strip()


if __name__ == "__main__":
    raise SystemExit(translation.main())
