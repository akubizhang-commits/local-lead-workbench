"""AWS trial D1: deployment settings (design §1.2, §10.1 test_config)."""
from datetime import timedelta

import pytest

from leadworkbench.config import LOOPBACK_ONLY, ConfigError, load

SERVER = {"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://3-1-2-4.sslip.io"}


def error(env):
    with pytest.raises(ConfigError) as info:
        load(env)
    return str(info.value)


def test_defaults_are_local_without_login():
    cfg = load({})
    assert (cfg.mode, cfg.auth, cfg.server, cfg.bind, cfg.port) == ("local", False, False, "127.0.0.1", 18765)
    assert cfg.allowed_hosts == {"127.0.0.1", "localhost", "::1"}
    assert cfg.backup_daily is False
    assert (cfg.cookie_name, cfg.cookie_secure) == ("lw_session", False)


def test_server_defaults():
    cfg = load(SERVER)
    assert cfg.server and cfg.auth and cfg.backup_daily
    assert cfg.port == 8000
    assert cfg.public_origin == "https://3-1-2-4.sslip.io"
    assert cfg.allowed_hosts == {"3-1-2-4.sslip.io", "127.0.0.1", "localhost", "::1"}
    assert (cfg.cookie_name, cfg.cookie_secure) == ("__Host-lw_session", True)
    assert (cfg.session_idle, cfg.session_max) == (timedelta(hours=24), timedelta(days=7))
    assert (cfg.backup_time, cfg.timezone, cfg.s3_bucket, cfg.s3_prefix) == ("03:30", "Asia/Shanghai", "", "leadworkbench/")


def test_server_requires_public_origin():
    assert "LEAD_PUBLIC_ORIGIN" in error({"LEAD_MODE": "server"})


def test_server_requires_login():
    assert "服务器模式必须开启登录" in error({**SERVER, "LEAD_AUTH": "off"})


def test_unknown_mode():
    assert "LEAD_MODE" in error({"LEAD_MODE": "cloud"})


@pytest.mark.parametrize("origin", ["http://lw.test", "https://lw.test/path", "https://user:pw@lw.test",
                                    "https://lw.test?x=1", "ftp://lw.test", "https://bad_host", "https://:443",
                                    "https://lw.test:99999"])
def test_bad_public_origin(origin):
    message = error({**SERVER, "LEAD_PUBLIC_ORIGIN": origin})
    assert "LEAD_PUBLIC_ORIGIN" in message
    assert origin not in message  # never echo values


def test_insecure_http_only_when_allowed():
    cfg = load({**SERVER, "LEAD_PUBLIC_ORIGIN": "http://lw.test:8080", "LEAD_ALLOW_INSECURE_HTTP": "1"})
    assert cfg.public_origin == "http://lw.test:8080"
    assert (cfg.cookie_name, cfg.cookie_secure) == ("lw_session", False)


def test_allowed_hosts_are_merged_with_public_host_and_loopback():
    cfg = load({**SERVER, "LEAD_ALLOWED_HOSTS": "other.example"})
    assert cfg.allowed_hosts == {"other.example", "3-1-2-4.sslip.io", "127.0.0.1", "localhost", "::1"}


@pytest.mark.parametrize("hosts", ["*", "a b", "x:80", ",".join(f"h{i}.example" for i in range(11)), "a" * 254])
def test_bad_allowed_hosts(hosts):
    assert "LEAD_ALLOWED_HOSTS" in error({**SERVER, "LEAD_ALLOWED_HOSTS": hosts})


@pytest.mark.parametrize("port", ["80", "70000", "abc", "-1"])
def test_bad_port(port):
    assert "LEAD_PORT" in error({"LEAD_PORT": port})


def test_bad_bind():
    assert "LEAD_BIND" in error({**SERVER, "LEAD_BIND": "example.com"})


def test_local_without_login_must_bind_loopback():
    assert LOOPBACK_ONLY in error({"LEAD_BIND": "0.0.0.0"})
    assert load({"LEAD_BIND": "::1"}).bind == "::1"
    assert LOOPBACK_ONLY in error({"LEAD_ALLOWED_HOSTS": "example.com"})
    assert load({"LEAD_ALLOWED_HOSTS": "localhost,127.0.0.2"}).allowed_hosts >= {"127.0.0.2"}


def test_local_with_login_may_bind_anywhere():
    cfg = load({"LEAD_AUTH": "on", "LEAD_BIND": "0.0.0.0"})
    assert cfg.auth and not cfg.server and cfg.bind == "0.0.0.0"
    assert (cfg.cookie_name, cfg.cookie_secure) == ("lw_session", False)


@pytest.mark.parametrize("bucket", ["ab", "Upper", "-start", "end-", "a..b", "192.168.1.1", "a" * 64, "under_score"])
def test_bad_bucket(bucket):
    message = error({**SERVER, "LEAD_BACKUP_S3_BUCKET": bucket, "LEAD_BACKUP_S3_REGION": "us-east-1"})
    assert "LEAD_BACKUP_S3_BUCKET" in message


def test_bucket_requires_region():
    assert "LEAD_BACKUP_S3_REGION" in error({**SERVER, "LEAD_BACKUP_S3_BUCKET": "my-bucket"})
    cfg = load({**SERVER, "LEAD_BACKUP_S3_BUCKET": "my-bucket", "LEAD_BACKUP_S3_REGION": "ap-northeast-1"})
    assert (cfg.s3_bucket, cfg.s3_region) == ("my-bucket", "ap-northeast-1")


@pytest.mark.parametrize("region", ["us-east", "US-EAST-1", "useast1", "us-east-1a"])
def test_bad_region(region):
    assert "LEAD_BACKUP_S3_REGION" in error({**SERVER, "LEAD_BACKUP_S3_BUCKET": "my-bucket",
                                             "LEAD_BACKUP_S3_REGION": region})


def test_prefix_rules():
    assert load({**SERVER, "LEAD_BACKUP_S3_PREFIX": "a/b"}).s3_prefix == "a/b/"
    assert load({**SERVER, "LEAD_BACKUP_S3_PREFIX": ""}).s3_prefix == ""
    for bad in ("/lead", "a/../b", "sp ace"):
        assert "LEAD_BACKUP_S3_PREFIX" in error({**SERVER, "LEAD_BACKUP_S3_PREFIX": bad})


def test_endpoint_rules():
    assert load({**SERVER, "LEAD_BACKUP_S3_ENDPOINT": "http://127.0.0.1:18854"}).s3_endpoint == "http://127.0.0.1:18854"
    for bad in ("http://127.0.0.1", "http://h:1/path", "ftp://h:1"):
        assert "LEAD_BACKUP_S3_ENDPOINT" in error({**SERVER, "LEAD_BACKUP_S3_ENDPOINT": bad})


@pytest.mark.parametrize("value", ["3:30", "24:00", "12:60", "noon"])
def test_bad_backup_time(value):
    assert "LEAD_BACKUP_TIME" in error({**SERVER, "LEAD_BACKUP_TIME": value})


def test_timezone():
    assert load({**SERVER, "LEAD_TIMEZONE": "Asia/Tokyo"}).timezone == "Asia/Tokyo"
    assert "LEAD_TIMEZONE" in error({**SERVER, "LEAD_TIMEZONE": "Mars/Base"})


def test_backup_settings_ignored_when_daily_backup_is_off():
    leftovers = {"LEAD_TIMEZONE": "Mars/Base", "LEAD_BACKUP_S3_REGION": "nowhere", "LEAD_BACKUP_TIME": "x"}
    assert load(leftovers).backup_daily is False
    assert "LEAD_TIMEZONE" in error({**leftovers, "LEAD_BACKUP_DAILY": "on"})


def test_session_limits():
    cfg = load({**SERVER, "LEAD_SESSION_IDLE_HOURS": "48", "LEAD_SESSION_MAX_DAYS": "2"})
    assert (cfg.session_idle, cfg.session_max) == (timedelta(hours=48), timedelta(days=2))
    assert "LEAD_SESSION_IDLE_HOURS" in error({**SERVER, "LEAD_SESSION_IDLE_HOURS": "0"})
    assert "LEAD_SESSION_IDLE_HOURS" in error({**SERVER, "LEAD_SESSION_IDLE_HOURS": "721"})
    assert "LEAD_SESSION_MAX_DAYS" in error({**SERVER, "LEAD_SESSION_MAX_DAYS": "91"})
    assert "LEAD_SESSION_MAX_DAYS" in error({**SERVER, "LEAD_SESSION_IDLE_HOURS": "72", "LEAD_SESSION_MAX_DAYS": "2"})
    # Only validated when login is on.
    assert load({"LEAD_SESSION_IDLE_HOURS": "0"}).auth is False


def test_errors_list_every_variable_without_values():
    secretish = "zz-pasted-secret-value-1234"
    message = error({"LEAD_MODE": "server", "LEAD_PORT": secretish, "LEAD_BIND": secretish})
    for name in ("LEAD_PUBLIC_ORIGIN", "LEAD_PORT", "LEAD_BIND"):
        assert name in message
    assert secretish not in message
