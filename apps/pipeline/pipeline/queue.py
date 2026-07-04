"""Redis-backed job queue.

Jobs are small JSON messages: {"type": <job type>, "entity_id": <uuid str>,
"batch_id": <uuid str|null>, "force": <bool>}. The worker BRPOPs and dispatches.
Job *state* lives in Postgres (`Job` rows) for observability/retry.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache

import redis

from .config import get_settings

QUEUE_KEY = "footpipe:jobs"


@dataclass
class JobMessage:
    type: str
    entity_id: str
    batch_id: str | None = None
    force: bool = False
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> "JobMessage":
        d = json.loads(raw)
        return JobMessage(
            type=d["type"],
            entity_id=d["entity_id"],
            batch_id=d.get("batch_id"),
            force=d.get("force", False),
            extra=d.get("extra", {}),
        )


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def enqueue(msg: JobMessage) -> None:
    get_redis().lpush(QUEUE_KEY, msg.to_json())


def dequeue(timeout: int = 2) -> JobMessage | None:
    res = get_redis().brpop(QUEUE_KEY, timeout=timeout)
    if res is None:
        return None
    _key, raw = res
    return JobMessage.from_json(raw)


def queue_depth() -> int:
    return get_redis().llen(QUEUE_KEY)
