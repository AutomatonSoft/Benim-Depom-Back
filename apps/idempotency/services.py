import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import IdempotencyRecord


class IdempotencyKeyReuseError(Exception):
    """The same key was used for another request payload."""


class IdempotencyRequestInProgressError(Exception):
    """The original request is still running."""


@dataclass
class IdempotencyClaim:
    record: IdempotencyRecord | None
    replay_status: int | None = None
    replay_body: Any | None = None

    @property
    def is_replay(self) -> bool:
        return self.replay_status is not None


def _get_key(request) -> str | None:
    key = request.headers.get("Idempotency-Key", "").strip()

    if not key:
        return None

    if len(key) > 128:
        raise ValidationError(
            {
                "Idempotency-Key": (
                    "Idempotency-Key must contain at most 128 characters."
                )
            }
        )

    return key


def _request_hash(data: Any) -> str:
    serialized = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def claim_idempotency_key(
    *, request, endpoint: str, payload: Any | None = None
) -> IdempotencyClaim:
    """
    Reserve a key for one authenticated user and logical endpoint.

    No Idempotency-Key means backward-compatible normal request processing.
    """
    key = _get_key(request)
    if key is None:
        return IdempotencyClaim(record=None)

    request_hash = _request_hash(request.data if payload is None else payload)
    expires_at = timezone.now() + timedelta(hours=settings.IDEMPOTENCY_TTL_HOURS)

    # A simultaneous request can hit the unique constraint. In that case,
    # read the winner's record and return its actual state.
    for _ in range(2):
        try:
            with transaction.atomic():
                record = (
                    IdempotencyRecord.objects.select_for_update()
                    .filter(
                        user=request.user,
                        endpoint=endpoint,
                        key=key,
                    )
                    .first()
                )

                if record is None:
                    record = IdempotencyRecord.objects.create(
                        user=request.user,
                        endpoint=endpoint,
                        key=key,
                        request_hash=request_hash,
                        status=IdempotencyRecord.Status.PROCESSING,
                        expires_at=expires_at,
                    )
                    return IdempotencyClaim(record=record)

                if record.expires_at <= timezone.now():
                    record.request_hash = request_hash
                    record.status = IdempotencyRecord.Status.PROCESSING
                    record.response_status = None
                    record.response_body = {}
                    record.expires_at = expires_at
                    record.save(
                        update_fields=(
                            "request_hash",
                            "status",
                            "response_status",
                            "response_body",
                            "expires_at",
                            "updated_at",
                        )
                    )
                    return IdempotencyClaim(record=record)

                if record.request_hash != request_hash:
                    raise IdempotencyKeyReuseError

                if record.status == IdempotencyRecord.Status.COMPLETED:
                    return IdempotencyClaim(
                        record=record,
                        replay_status=record.response_status,
                        replay_body=record.response_body,
                    )

                raise IdempotencyRequestInProgressError

        except IntegrityError:
            continue

    raise IdempotencyRequestInProgressError


def complete_idempotency_claim(
    *,
    claim: IdempotencyClaim,
    response_status: int,
    response_body: Any,
) -> None:
    """Persist the successful API response for later replay."""
    if claim.record is None:
        return

    IdempotencyRecord.objects.filter(
        pk=claim.record.pk,
        status=IdempotencyRecord.Status.PROCESSING,
    ).update(
        status=IdempotencyRecord.Status.COMPLETED,
        response_status=response_status,
        response_body=_json_ready(response_body),
    )


def abandon_idempotency_claim(*, claim: IdempotencyClaim) -> None:
    """
    Remove an unfinished record after validation/business failure.

    Thus the client can fix its request and safely retry with the same key.
    """
    if claim.record is None:
        return

    IdempotencyRecord.objects.filter(
        pk=claim.record.pk,
        status=IdempotencyRecord.Status.PROCESSING,
    ).delete()
