"""AWS trial D2: login rate limiter (design §2.7, §10.1 test_login_limiter)."""
from leadworkbench import login_limiter as ll
from leadworkbench.login_limiter import LoginLimiter


def fail(limiter, user, ip, now):
    assert limiter.reserve(user, ip, now) is None
    return limiter.finish(user, ip, False, now)


def test_account_locks_after_five_failures():
    limiter = LoginLimiter()
    for i in range(4):
        assert fail(limiter, "alice", f"10.0.0.{i}", 100) == []
    assert fail(limiter, "alice", "10.0.0.9", 100) == ["account"]
    assert limiter.reserve("alice", "10.0.1.1", 101) == 15 * 60 - 1
    assert limiter.reserve("bob", "10.0.1.1", 101) is None  # other accounts unaffected
    # Lock and window both end after 15 minutes.
    assert limiter.reserve("alice", "10.0.1.1", 100 + 15 * 60 + 1) is None


def test_ip_locks_after_twenty_failures():
    limiter = LoginLimiter()
    for i in range(19):
        assert fail(limiter, f"user{i}", "1.2.3.4", 10) == []
    assert fail(limiter, "user19", "1.2.3.4", 10) == ["ip"]
    assert limiter.reserve("fresh", "1.2.3.4", 11) is not None
    assert limiter.reserve("fresh", "1.2.3.5", 11) is None


def test_ipv6_shares_a_slash_64():
    limiter = LoginLimiter()
    for i in range(20):
        fail(limiter, f"u{i}", f"2001:db8:1:2::{i + 1:x}", 10)
    assert limiter.reserve("x", "2001:db8:1:2:ffff::1", 11) is not None
    assert limiter.reserve("x", "2001:db8:1:3::1", 11) is None
    assert ll.ip_key("not-an-ip") == "not-an-ip"


def test_window_slides():
    limiter = LoginLimiter()
    for t in (0, 100, 200, 300):
        fail(limiter, "alice", "9.9.9.9", t)
    # Oldest failure (t=0) leaves the window at 900: a fifth attempt in between is allowed.
    assert limiter.reserve("alice", "9.9.9.9", 950) is None
    limiter.finish("alice", "9.9.9.9", False, 950)
    assert limiter.reserve("alice", "9.9.9.9", 1199) is None  # 100 and 200 left the window
    limiter.finish("alice", "9.9.9.9", None, 1199)


def test_success_clears_counters_and_trusts_ip():
    limiter = LoginLimiter()
    for _ in range(4):
        fail(limiter, "alice", "5.5.5.5", 10)
    assert limiter.reserve("alice", "5.5.5.5", 11) is None
    limiter.finish("alice", "5.5.5.5", True, 11)
    assert limiter.is_trusted("5.5.5.5", 12)
    for _ in range(4):
        fail(limiter, "alice", "5.5.5.5", 12)
    assert limiter.reserve("alice", "5.5.5.5", 13) is None
    assert not limiter.is_trusted("5.5.5.5", 11 + 7 * 86400 + 1)


def test_inflight_counts_against_the_limit():
    limiter = LoginLimiter()
    results = [limiter.reserve("alice", "7.7.7.7", 1) for _ in range(20)]
    assert results.count(None) == 5
    assert all(r is not None and r >= 1 for r in results if r is not None)


def test_storage_error_releases_without_counting():
    limiter = LoginLimiter()
    for _ in range(10):
        assert limiter.reserve("alice", "7.7.7.7", 1) is None
        limiter.finish("alice", "7.7.7.7", None, 1)
    assert limiter.accounts[ll.account_key("alice")].failures == ll.deque()


def test_global_only_applies_to_untrusted_ips():
    limiter = LoginLimiter()
    limiter.global_bucket.limit = 3
    assert limiter.reserve("good", "8.8.8.8", 1) is None
    limiter.finish("good", "8.8.8.8", True, 1)
    locked = []
    for i in range(3):
        locked += fail(limiter, f"spray{i}", f"6.6.6.{i}", 2)
    assert locked == ["global"]
    assert limiter.reserve("other", "6.6.7.1", 3) is not None  # untrusted: refused
    assert limiter.reserve("good", "8.8.8.8", 3) is None       # trusted: allowed
    limiter.finish("good", "8.8.8.8", False, 3)
    assert limiter.reserve("other", "6.6.7.1", 2 + 61) is None


def test_eviction_keeps_inflight_and_locked_buckets():
    limiter = LoginLimiter(max_buckets=3)
    assert limiter.reserve("held", "1.1.1.1", 1) is None  # in flight
    for _ in range(5):
        fail(limiter, "locked", "1.1.1.2", 1)
    for name in ("a1x", "b1x", "c1x", "d1x"):
        fail(limiter, name, "1.1.1.3", 1)
    assert ll.account_key("held") in limiter.accounts
    assert ll.account_key("locked") in limiter.accounts
    assert len(limiter.accounts) <= 3
    limiter.finish("held", "1.1.1.1", False, 1)


def test_account_bucket_key_is_hashed():
    limiter = LoginLimiter()
    fail(limiter, "secret-user-name", "1.1.1.1", 1)
    (key,) = limiter.accounts
    assert len(key) == 32 and "secret" not in key
