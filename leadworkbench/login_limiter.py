"""In-memory login rate limiter (AWS trial, design §2.7, O2).

Three bucket kinds: account (sha256 of the normalised user name, unknown names
count too), IP (IPv4 address or IPv6 /64) and one global bucket that only counts
and refuses IPs without a recent successful login ("trusted"). ``reserve`` runs
before hashing and counts the attempt as in flight, so concurrent requests
cannot overshoot a limit; ``finish`` settles it. Both are synchronous and are
called from the event loop thread without an ``await`` in between, which makes
each call atomic. Counters reset when the API process restarts.
"""
from __future__ import annotations
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import hashlib
import ipaddress
import math

ACCOUNT_LIMIT, ACCOUNT_WINDOW, ACCOUNT_LOCK = 5, 15 * 60, 15 * 60
IP_LIMIT, IP_WINDOW, IP_LOCK = 20, 15 * 60, 15 * 60
GLOBAL_LIMIT, GLOBAL_WINDOW, GLOBAL_LOCK = 100, 60, 60
TRUSTED_DAYS = 7
TRUSTED_MAX = 1000
BUCKETS_MAX = 10_000


@dataclass
class Bucket:
    limit: int
    window: float
    lock: float
    scope: str
    failures: deque = field(default_factory=deque)
    inflight: int = 0
    locked_until: float = 0.0

    def prune(self, now):
        while self.failures and self.failures[0] <= now - self.window:
            self.failures.popleft()

    def retry_after(self, now):
        if not self.failures:
            return 1
        return max(1, math.ceil(self.failures[0] + self.window - now))

    def idle(self, now):
        return self.inflight == 0 and self.locked_until <= now


def account_key(username_key: str) -> str:
    return hashlib.sha256(username_key.encode("utf-8")).hexdigest()[:32]


def ip_key(ip: str) -> str:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


class LoginLimiter:
    def __init__(self, max_buckets=BUCKETS_MAX):
        self.max_buckets = max_buckets
        self.accounts: OrderedDict[str, Bucket] = OrderedDict()
        self.ips: OrderedDict[str, Bucket] = OrderedDict()
        self.global_bucket = Bucket(GLOBAL_LIMIT, GLOBAL_WINDOW, GLOBAL_LOCK, "global")
        self.trusted: OrderedDict[str, float] = OrderedDict()
        self._reserved: dict[tuple[str, str], list[list[Bucket]]] = {}

    # -- helpers --
    def _get(self, table, key, factory, now):
        bucket = table.get(key)
        if bucket is None:
            bucket = table[key] = factory()
            self._evict(table, now, keep=key)
        else:
            table.move_to_end(key)
        return bucket

    def _evict(self, table, now, keep):
        """Drop the least recently touched idle buckets (never in flight, locked or new)."""
        if len(table) <= self.max_buckets:
            return
        for key in list(table):
            if len(table) <= self.max_buckets:
                break
            if key != keep and table[key].idle(now):
                del table[key]

    def is_trusted(self, ipk, now):
        seen = self.trusted.get(ipk)
        if seen is None:
            return False
        if seen <= now - TRUSTED_DAYS * 86400:
            del self.trusted[ipk]
            return False
        return True

    def _buckets(self, username_key, ip, now):
        akey, ipk = account_key(username_key), ip_key(ip)
        buckets = [self._get(self.accounts, akey, lambda: Bucket(ACCOUNT_LIMIT, ACCOUNT_WINDOW, ACCOUNT_LOCK,
                                                                     "account"), now),
                   self._get(self.ips, ipk, lambda: Bucket(IP_LIMIT, IP_WINDOW, IP_LOCK, "ip"), now)]
        if not self.is_trusted(ipk, now):
            buckets.append(self.global_bucket)
        return (akey, ipk), buckets

    # -- API --
    def reserve(self, username_key: str, ip: str, now: float) -> int | None:
        """Retry-After seconds when refused; None when the attempt may proceed."""
        handle, buckets = self._buckets(username_key, ip, now)
        for bucket in buckets:
            if bucket.locked_until > now:
                return max(1, math.ceil(bucket.locked_until - now))
        for bucket in buckets:
            bucket.prune(now)
            if len(bucket.failures) + bucket.inflight + 1 > bucket.limit:
                return bucket.retry_after(now)
        for bucket in buckets:
            bucket.inflight += 1
        self._reserved.setdefault(handle, []).append(buckets)
        return None

    def finish(self, username_key: str, ip: str, ok: bool | None, now: float) -> list[str]:
        """Settle one reservation. ``ok=None`` releases it without counting (storage error).
        Returns the scopes that became locked by this failure."""
        handle = (account_key(username_key), ip_key(ip))
        stack = self._reserved.get(handle)
        if not stack:
            return []
        buckets = stack.pop()
        if not stack:
            del self._reserved[handle]
        for bucket in buckets:
            bucket.inflight -= 1
        if ok is None:
            return []
        if ok:
            for bucket in buckets[:2]:
                bucket.failures.clear()
                bucket.locked_until = 0.0
            self.trusted[handle[1]] = now
            self.trusted.move_to_end(handle[1])
            while len(self.trusted) > TRUSTED_MAX:
                self.trusted.popitem(last=False)
            return []
        locked = []
        for bucket in buckets:
            bucket.prune(now)
            bucket.failures.append(now)
            if len(bucket.failures) >= bucket.limit and bucket.locked_until <= now:
                bucket.locked_until = now + bucket.lock
                locked.append(bucket.scope)
        return locked
