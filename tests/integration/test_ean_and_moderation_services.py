import pytest
from rest_framework.exceptions import ValidationError

from apps.ean.models import EanCode
from apps.ean.services import (
    assign_ean_codes_to_product,
    get_ean_summary,
    import_ean_codes,
)
from apps.moderation.services import (
    approve_product,
    reject_product,
    submit_product_for_moderation,
)
from apps.products.models import Product


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_ean_import_assignment_is_atomic_and_summary_is_correct(
    seller, manager, product_factory
):
    result = import_ean_codes(
        account=EanCode.Account.JV,
        raw_codes="4006381333931\n4006381333931\ninvalid",
        imported_by=manager,
    )
    assert result["created_count"] == 1 and result["invalid_count"] == 1
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    with pytest.raises(ValidationError, match="No free EAN"):
        assign_ean_codes_to_product(product=product)
    assert not EanCode.objects.filter(product=product).exists()

    EanCode.objects.create(
        code="9501101530003", account=EanCode.Account.XL, imported_by=manager
    )
    assigned = assign_ean_codes_to_product(product=product)
    assert {code.account for code in assigned} == {"jv", "xl"}
    product.refresh_from_db()
    assert product.ean_jv == "4006381333931"
    assert product.ean_xl == "9501101530003"
    assert assign_ean_codes_to_product(product=product) == assigned
    summary = get_ean_summary()
    assert summary["requires_attention"] is True


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_moderation_services_validate_states_and_store_decisions(
    seller, manager, product_factory, product_image_factory
):
    draft = product_factory(owner=seller)
    with pytest.raises(ValidationError, match="image"):
        submit_product_for_moderation(product=draft)
    product_image_factory(product=draft)
    submitted = submit_product_for_moderation(product=draft)
    assert submitted.status == Product.Status.SUBMITTED
    with pytest.raises(ValidationError, match="cannot be submitted"):
        submit_product_for_moderation(product=submitted)

    rejected = reject_product(product=submitted, manager=manager, comment="Bad image")
    assert rejected.status == Product.Status.REJECTED
    with pytest.raises(ValidationError, match="Only submitted"):
        reject_product(product=rejected, manager=manager, comment="again")

    ready = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    EanCode.objects.create(code="4006381333931", account="jv", imported_by=manager)
    EanCode.objects.create(code="9501101530003", account="xl", imported_by=manager)
    approved = approve_product(product=ready, manager=manager, comment="ok")
    assert approved.status == Product.Status.APPROVED
    assert approved.moderation_decisions.count() == 1


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_manager_can_move_approved_product_without_live_listings(
    seller, manager, product_factory
):
    from apps.moderation.models import ModerationDecision
    from apps.moderation.services import change_approved_product_status
    from apps.orchestrator.models import MarketplacePublication

    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4012345678901",
        ean_xl="4012345678902",
    )
    with pytest.raises(ValidationError, match="seller must edit"):
        change_approved_product_status(
            product=product_factory(owner=seller, status=Product.Status.REJECTED),
            manager=manager,
            status=Product.Status.SUBMITTED,
        )

    returned = change_approved_product_status(
        product=product,
        manager=manager,
        status=Product.Status.SUBMITTED,
    )
    assert returned.status == Product.Status.SUBMITTED
    assert returned.ean_jv == "4012345678901"
    assert returned.moderation_decisions.filter(
        decision=ModerationDecision.Decision.RETURNED_TO_REVIEW
    ).exists()

    EanCode.objects.create(code="4012345678901", account="jv", imported_by=manager)
    EanCode.objects.create(code="4012345678902", account="xl", imported_by=manager)
    reapproved = approve_product(product=returned, manager=manager, comment="again")
    MarketplacePublication.objects.create(
        product=reapproved,
        marketplace=MarketplacePublication.Marketplace.OTTO,
        account=MarketplacePublication.Account.JV,
        ean=reapproved.ean_jv,
        status=MarketplacePublication.Status.ACTIVE,
    )
    with pytest.raises(ValidationError, match="Deactivate marketplace listings"):
        change_approved_product_status(
            product=reapproved,
            manager=manager,
            status=Product.Status.REJECTED,
            comment="too late",
        )
