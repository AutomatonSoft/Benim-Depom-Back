import pytest

from apps.marketplace.listing_title import with_listing_brand_mark


@pytest.mark.unit
@pytest.mark.parametrize("suffix", ["", " (BD)", " BD", " bd  "])
def test_appends_bd_mark_once(suffix):
    assert (
        with_listing_brand_mark(
            f"Esszimmerstuhl aus Holz in Terrakotta{suffix}",
            max_length=70,
        )
        == "Esszimmerstuhl aus Holz in Terrakotta BD"
    )


@pytest.mark.unit
def test_fits_brand_mark_into_max_length():
    title = "A" * 70
    marked = with_listing_brand_mark(title, max_length=70)
    assert marked.endswith(" BD")
    assert len(marked) == 70
    assert with_listing_brand_mark(marked, max_length=70) == marked
