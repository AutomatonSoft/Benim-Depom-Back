import json
from functools import lru_cache
from uuid import UUID

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError

from .errors import BulkEndError

JOB_TTL_SECONDS = 7 * 24 * 60 * 60
ACCOUNT_LOCK_SECONDS = 15 * 60
KEY_PREFIX = "ebay-bulk-end:v1"


@lru_cache(maxsize=2)
def _redis_for_url(url):
    if not isinstance(url, str) or not url.startswith(("redis://", "rediss://")):
        raise BulkEndError("Хранилище фоновых заданий недоступно.")
    try:
        return Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5
        )
    except (RedisError, ValueError) as error:
        raise BulkEndError("Хранилище фоновых заданий недоступно.") from error


def redis_client():
    return _redis_for_url(getattr(settings, "CELERY_RESULT_BACKEND", ""))


def job_key(job_id):
    return KEY_PREFIX + ":job:" + str(UUID(str(job_id)))


def lock_key(environment, account):
    return KEY_PREFIX + ":account-lock:" + environment + ":" + account


def cancellation_key(job_id):
    return KEY_PREFIX + ":cancel:" + str(UUID(str(job_id)))


def save_job(job):
    try:
        redis_client().set(
            job_key(job["id"]), json.dumps(job, ensure_ascii=False), ex=JOB_TTL_SECONDS
        )
    except (RedisError, TypeError, ValueError) as error:
        raise BulkEndError("Не удалось сохранить состояние задания.") from error


def load_job(job_id):
    try:
        raw = redis_client().get(job_key(job_id))
        return json.loads(raw) if raw else None
    except (RedisError, TypeError, ValueError) as error:
        raise BulkEndError("Не удалось прочитать состояние задания.") from error


def request_cancellation(job_id):
    try:
        redis_client().set(cancellation_key(job_id), "1", ex=JOB_TTL_SECONDS)
    except (RedisError, TypeError, ValueError) as error:
        raise BulkEndError("Не удалось сохранить запрос на остановку.") from error


def cancellation_requested(job_id):
    try:
        return redis_client().get(cancellation_key(job_id)) == "1"
    except (RedisError, TypeError, ValueError) as error:
        raise BulkEndError("Не удалось проверить запрос на остановку.") from error


def acquire_account_lock(environment, account, job_id):
    client = redis_client()
    key = lock_key(environment, account)
    owner = str(job_id)
    renewed = client.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
        1,
        key,
        owner,
        ACCOUNT_LOCK_SECONDS,
    )
    if renewed:
        return True
    return bool(client.set(key, owner, nx=True, ex=ACCOUNT_LOCK_SECONDS))


def release_account_lock(environment, account, job_id):
    client = redis_client()
    key = lock_key(environment, account)
    client.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
        1,
        key,
        str(job_id),
    )
