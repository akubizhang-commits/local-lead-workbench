"""Offline Maps stability regressions; all processes and state use tmp_path."""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import format_datetime

import pytest

from leadworkbench import market_plan
from leadworkbench.sources import maps
from test_maps import ROW, fake
from test_market_plan import CITY, CONFIG


@pytest.fixture(autouse=True)
def no_catalogue_fallback(monkeypatch):
    """These tests cover the geocoder path; the P0-5 catalogue fallback is tested in test_market_relocate.py."""
    monkeypatch.setattr(market_plan, "_catalogue_fallback", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def isolated_maps_binary(monkeypatch):
    monkeypatch.delenv("MAPS_SCRAPER_BIN", raising=False)


@pytest.mark.parametrize("error,reason", [
    ("no captcha detected; HTTP 429 Too Many Requests", "rate_limited"),
    ("captcha false; HTTP 403 access denied", "access_denied"),
])
async def test_negative_captcha_does_not_hide_an_access_restriction(
        tmp_path, monkeypatch, error, reason):
    event = json.dumps(dict(level="error", error=error))
    fake(tmp_path, monkeypatch,
         f"out.write_text({json.dumps(ROW)!r})\n"
         f"print({event!r}, file=sys.stderr)\n")
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["stop_reason"] == reason
    assert result["status"] == "blocked"
    assert len(result["records"]) == 1
    assert not result["exhausted"]


@pytest.mark.parametrize("error,reason", [
    ("context deadline exceeded", "timeout"),
    ("playwright: net::ERR_CONNECTION_RESET", "adapter_error"),
])
@pytest.mark.parametrize("with_records", [False, True])
async def test_failed_internal_job_is_retried_by_queue_after_zero_exit(
        tmp_path, monkeypatch, error, reason, with_records):
    event = json.dumps(dict(level="info", message="job finished", status="failed", error=error))
    fake(tmp_path, monkeypatch,
         f"out.write_text({json.dumps(ROW) if with_records else ''!r})\n"
         f"print({event!r}, file=sys.stderr)\n")
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["stop_reason"] == reason
    assert result["status"] == ("partial" if with_records else "blocked")
    assert len(result["records"]) == int(with_records)
    assert not result["exhausted"]


def test_final_log_scan_reaches_restrictions_after_multiple_read_chunks(tmp_path):
    path = tmp_path / "stderr.log"
    line = json.dumps(dict(level="info", message="progress " + "." * 1000)) + "\n"
    path.write_text(line * 800 + json.dumps(dict(level="error", error="captcha required")) + "\n")
    reader = maps.RestrictionLog(path)
    assert reader.poll() is None
    assert reader.poll(final=True) == "captcha"


def test_restriction_after_an_incomplete_log_line_is_not_lost(tmp_path):
    path = tmp_path / "stderr.log"
    event = json.dumps(dict(level="error", error="captcha not solved"))
    path.write_text(event[:-4])
    reader = maps.RestrictionLog(path)
    assert reader.poll() is None
    with path.open("a") as stream:
        stream.write(event[-4:])  # An exited child need not leave a final newline.
    assert reader.poll(final=True) == "captcha"


def test_oversized_log_line_cannot_supply_a_false_json_tail(tmp_path):
    path = tmp_path / "stderr.log"
    event = json.dumps(dict(level="error", error="captcha required"))
    path.write_text("." * 300_000 + event + "\n")
    reader = maps.RestrictionLog(path)
    assert reader.poll() is None
    assert reader.poll(final=True) is None
    with path.open("a") as stream:
        stream.write(event + "\n")
    assert reader.poll(final=True) == "captcha"


@pytest.fixture
def city_clock(tmp_path, monkeypatch):
    clock = [10_000.0]
    monkeypatch.setattr(market_plan.time, "time", lambda: clock[0])
    monkeypatch.setattr(market_plan, "RATE_LIMIT_FILE", tmp_path / "city-rate.lock")

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(market_plan.asyncio, "sleep", sleep)
    return clock


@pytest.fixture
def city_http(monkeypatch):
    """Fake only the HTTP transport; use actual request, cache and limiter code."""
    response_config = dict(status=429, headers={})
    calls = []

    class Content:
        async def iter_chunked(self, size):
            yield json.dumps([CITY]).encode()

    class Response:
        content_length = None
        content = Content()

        async def __aenter__(self):
            self.status = response_config["status"]
            self.headers = response_config["headers"]
            return self

        async def __aexit__(self, *args):
            pass

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, endpoint, **kwargs):
            calls.append(kwargs["params"])
            return Response()

    monkeypatch.setattr(market_plan.aiohttp, "TCPConnector", lambda **kwargs: object())
    monkeypatch.setattr(market_plan.aiohttp, "ClientSession", Session)
    return response_config, calls


@pytest.mark.parametrize("header", [
    "7200",
    format_datetime(datetime.fromtimestamp(17_200, timezone.utc), usegmt=True),
])
@pytest.mark.parametrize("status", [429, 503])
async def test_retry_after_defers_other_city_and_data_directory_without_sleeping(
        tmp_path, city_clock, city_http, header, status):
    response, calls = city_http
    response.update(status=status, headers={"Retry-After": header})
    first = await market_plan.plan_market(CONFIG, tmp_path / "first")
    assert first["mode"] == "text_only"
    city_clock[0] = 10_901  # The ordinary 15-minute failed-city cache has expired.
    second = await market_plan.plan_market({**CONFIG, "city": "Other City"}, tmp_path / "second")
    assert second["mode"] == "text_only"
    assert (await market_plan.plan_market(CONFIG, tmp_path / "first"))["mode"] == "text_only"
    assert len(calls) == 1
    assert city_clock[0] == 10_901  # Do not occupy the Maps lane for a two-hour wait.
    city_clock[0] = 17_201
    response.update(status=200, headers={})
    recovered = await market_plan.plan_market(CONFIG, tmp_path / "first")
    assert recovered["mode"] == "grid"
    assert len(calls) == 2


@pytest.mark.parametrize("header", [None, "invalid", "NaN", "-1", "0"])
async def test_unusable_retry_after_still_cools_other_cities_for_fifteen_minutes(
        tmp_path, city_clock, city_http, header):
    response, calls = city_http
    response["headers"] = {"Retry-After": header} if header is not None else {}
    assert (await market_plan.plan_market(CONFIG, tmp_path / "first"))["mode"] == "text_only"
    city_clock[0] = 10_899
    other = await market_plan.plan_market({**CONFIG, "city": "Other City"}, tmp_path / "second")
    assert other["mode"] == "text_only"
    assert len(calls) == 1 and city_clock[0] == 10_899
    response.update(status=200, headers={})
    city_clock[0] = 10_901
    assert (await market_plan.plan_market(CONFIG, tmp_path / "first"))["mode"] == "grid"
    assert len(calls) == 2


async def test_existing_numeric_rate_reservation_keeps_normal_spacing(
        tmp_path, city_clock, city_http):
    response, calls = city_http
    response["status"] = 200
    market_plan.RATE_LIMIT_FILE.write_text("10000")
    city_clock[0] = 10_002
    assert (await market_plan.plan_market(CONFIG, tmp_path))["mode"] == "grid"
    assert len(calls) == 1 and city_clock[0] == 10_015


async def test_clock_rollback_defers_geocoding_without_a_long_sleep(
        tmp_path, city_clock, city_http):
    _, calls = city_http
    market_plan.RATE_LIMIT_FILE.write_text("20000")
    assert (await market_plan.plan_market(CONFIG, tmp_path))["mode"] == "text_only"
    assert not calls and city_clock[0] == 10_000


@pytest.mark.parametrize("address", [
    {"country_code": "us", "city": "Los Angeles", "state": "New York"},
    {"country_code": "us", "city": "Sacramento", "state": "California"},
])
async def test_explicit_geocoder_region_mismatch_uses_text_fallback(
        tmp_path, monkeypatch, city_clock, address):
    async def fetch(*args):
        return [{**CITY, "address": address}]

    monkeypatch.setattr(market_plan, "_fetch_city", fetch)
    plan = await market_plan.plan_market(CONFIG, tmp_path)
    assert plan["mode"] == "text_only"
    assert plan["warnings"]
    assert all(job["geo"] is None and job["bbox"] is None and job["level"] == 0
               for job in plan["jobs"])
    assert all("Los Angeles, California, US" in job["query"] for job in plan["jobs"])


@pytest.mark.parametrize("state", ["California", "CA"])
async def test_matching_geocoder_names_and_state_abbreviation_keep_geometry(
        tmp_path, monkeypatch, city_clock, state):
    async def fetch(*args):
        return [{**CITY, "address": {
            "country_code": "us", "city": " LOS  ANGELES ", "state": "California",
            "ISO3166-2-lvl4": "US-CA",
        }}]

    monkeypatch.setattr(market_plan, "_fetch_city", fetch)
    plan = await market_plan.plan_market({**CONFIG, "state": state}, tmp_path)
    assert plan["mode"] == "grid"
    assert not plan["warnings"]


async def test_success_cache_is_revalidated_against_requested_region(
        tmp_path, monkeypatch, city_clock):
    async def fetch(*args):
        return [CITY]

    monkeypatch.setattr(market_plan, "_fetch_city", fetch)
    assert (await market_plan.plan_market(CONFIG, tmp_path))["mode"] == "grid"
    cache = next((tmp_path / "geocoding").glob("*.json"))
    payload = json.loads(cache.read_text())
    payload["response"][0]["address"] = dict(country_code="us", city="Los Angeles", state="New York")
    cache.write_text(json.dumps(payload))
    assert (await market_plan.plan_market(CONFIG, tmp_path))["mode"] == "text_only"


@pytest.fixture
def stability_patch(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "maps_stability_patch", root / "scripts/patch_maps_stability.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = root / "vendor" / f"google-maps-scraper-{module.PINNED_COMMIT}"
    if not (source / ".git").exists():
        pytest.skip(f"缺少 Maps 源码 checkout（{source.relative_to(root)}），需先运行 scripts/install_maps.sh")
    checkout = tmp_path / "vendor"
    checkout.mkdir()
    for name in ("gmaps/job.go", "gmaps/place.go", "gmaps/searchjob.go", "main.go", "go.mod"):
        original = subprocess.check_output(
            ["git", "-C", str(source), "show", f"{module.PINNED_COMMIT}:{name}"])
        path = checkout / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(original)
    (checkout / "README.md").write_text("fixture upstream\n")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Offline Fixture",
                    "-c", "user.email=fixture@example.invalid",
                    "-c", "core.hooksPath=/dev/null", "commit", "-q", "--no-gpg-sign",
                    "-m", "offline fixture"], check=True)
    revision = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.setattr(module, "PINNED_COMMIT", revision)
    return module, checkout


def test_scraper_patch_replays_without_overwriting_unrelated_vendor_changes(stability_patch):
    patch, checkout = stability_patch
    path = checkout / "gmaps/job.go"
    path.write_text("// Existing local change stays intact.\n" + path.read_text())
    (checkout / "README.md").write_text("existing uncommitted change\n")
    patch.apply_patch(checkout)
    first = {str(p.relative_to(checkout)): p.read_bytes() for p in checkout.rglob("*.go")}
    patch.apply_patch(checkout)
    patch.verify_patch(checkout)
    assert first == {str(p.relative_to(checkout)): p.read_bytes() for p in checkout.rglob("*.go")}
    assert path.read_text().startswith("// Existing local change stays intact.\n")
    assert (checkout / "README.md").read_text() == "existing uncommitted change\n"


def test_scraper_patch_conflict_fails_before_changing_any_file(stability_patch):
    patch, checkout = stability_patch
    path = checkout / "gmaps/place.go"
    path.write_text(path.read_text().replace("defaultMaxRetries = 3", "defaultMaxRetries = 9"))
    before = {str(p.relative_to(checkout)): p.read_bytes() for p in checkout.rglob("*.go")}
    with pytest.raises(ValueError, match="mismatch|conflict"):
        patch.apply_patch(checkout)
    assert before == {str(p.relative_to(checkout)): p.read_bytes() for p in checkout.rglob("*.go")}


def test_scraper_patch_rejects_wrong_revision(stability_patch, monkeypatch):
    patch, checkout = stability_patch
    monkeypatch.setattr(patch, "PINNED_COMMIT", "0" * 40)
    with pytest.raises(ValueError, match="revision"):
        patch.apply_patch(checkout)
    assert not (checkout / "gmaps/local_stability.go").exists()


def test_scraper_patch_rejects_changed_safety_policy(stability_patch):
    patch, checkout = stability_patch
    patch.apply_patch(checkout)
    path = checkout / "gmaps/local_stability.go"
    path.write_text(path.read_text().replace("5 * time.Second", "1 * time.Second"))
    with pytest.raises(ValueError, match="mismatch|conflict"):
        patch.verify_patch(checkout)


def test_scraper_manifest_requires_compiled_policy_and_preserves_old_manifest(
        stability_patch, tmp_path):
    patch, checkout = stability_patch
    patch.apply_patch(checkout)
    candidate = tmp_path / "scraper.new"
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"previous":true}')
    info = {**patch.EXPECTED_INFO, "place_max_retries": 3}
    candidate.write_text("#!/bin/sh\nprintf '%s\\n' '" + json.dumps(info) + "'\n")
    candidate.chmod(0o755)
    with pytest.raises(ValueError, match="compiled"):
        patch.write_manifest(checkout, candidate, manifest)
    assert json.loads(manifest.read_text()) == {"previous": True}
    candidate.write_text("#!/bin/sh\nprintf '%s\\n' '" + json.dumps(patch.EXPECTED_INFO) + "'\n")
    patch.write_manifest(checkout, candidate, manifest)
    saved = json.loads(manifest.read_text())
    assert saved["stability"] == patch.EXPECTED_INFO
    assert saved["commit"] == patch.PINNED_COMMIT
    assert len(saved["binary_sha256"]) == 64 and len(saved["patch_sha256"]) == 64


@pytest.fixture
def installer_sandbox(tmp_path):
    """Exercise installer branches with fake git/Go/Python; no network is possible."""
    root = tmp_path / "delivery"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/install_maps.sh",
                    scripts / "install_maps.sh")
    commands = tmp_path / "commands"
    commands.mkdir()
    shim = commands / "shim"
    shim.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
with open(os.environ["INSTALL_EVENTS"], "a") as stream:
    stream.write(json.dumps([name, args, os.environ.get("PLAYWRIGHT_INSTALL_ONLY")]) + "\n")
if name == "git":
    if args[0] == "-C":
        target = pathlib.Path(args[1]); args = args[2:]
    else:
        target = pathlib.Path.cwd()
    if args[0] == "init":
        if len(args) > 1: target = pathlib.Path(args[-1])
        (target / ".git").mkdir(exist_ok=True)
    elif args[0] == "remote" or args[0] == "fetch":
        pass
    elif args[0] == "checkout":
        (target / ".revision").write_text(args[-1])
        (target / "go.mod").write_text("module fixture\n\ngo 1.27.1\n")
    elif args[0] == "rev-parse":
        if args[-1] == "--show-toplevel": print(target)
        else: print((target / ".revision").read_text())
    else:
        sys.exit(91)
elif name == "go":
    if args == ["version"]:
        old = os.environ.get("OLD_GO") == "1" and sys.argv[0] == os.environ["INSTALL_BASE_GO"]
        print("go version go1.26.3 darwin/arm64" if old else "go version go1.27.1 darwin/arm64")
    elif args[:1] == ["env"]:
        cache = pathlib.Path(os.environ["INSTALL_CACHE"])
        if args[-1] == "GOMODCACHE": print(cache)
        elif args[-1] == "GOHOSTOS": print("darwin")
        elif args[-1] == "GOHOSTARCH": print("arm64")
        elif args[-1] == "GOROOT":
            if os.environ.get("GOTOOLCHAIN") != "go1.27.1": sys.exit(96)
            toolchain = cache / "golang.org/toolchain@v0.0.1-go1.27.1.darwin-arm64"
            (toolchain / "bin").mkdir(parents=True)
            compiler = toolchain / "bin/go"
            shutil.copyfile(os.environ["INSTALL_SHIM"], compiler); compiler.chmod(0o755)
            print(toolchain)
        else: sys.exit(97)
    elif args[0] in {"mod", "test"}: pass
    elif args[0] == "build":
        path = pathlib.Path(args[args.index("-o") + 1])
        shutil.copyfile(os.environ["INSTALL_SHIM"], path); path.chmod(0o755)
    else: sys.exit(92)
elif name == "python":
    if "--source-digest" in args: print("a" * 64)
    if "--manifest" in args:
        pathlib.Path(args[args.index("--manifest") + 2]).write_text('{"fixture":"verified"}')
elif name.startswith("google-maps-scraper"):
    if os.environ.get("PLAYWRIGHT_INSTALL_ONLY") == "1":
        if os.environ.get("FAIL_BROWSER") == "1": sys.exit(93)
    elif args == ["-h"]: print("fixture help")
    elif args == ["-local-stability-info"]: print("{}")
    else: sys.exit(94)
else:
    sys.exit(95)
''')
    shim.chmod(0o755)
    for name in ("git", "go", "python"):
        (commands / name).symlink_to(shim)
    env = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"],
           "GO": str(commands / "go"), "PYTHON": str(commands / "python"),
           "INSTALL_EVENTS": str(tmp_path / "events.jsonl"), "INSTALL_SHIM": str(shim),
           "INSTALL_CACHE": str(tmp_path / "cache"), "INSTALL_BASE_GO": str(commands / "go")}
    env.pop("MAPS_COMMIT", None)
    source = root / "vendor/google-maps-scraper-549e4b5e61c7103685ef8392f246ebdba783ed03"

    def prepare(revision="549e4b5e61c7103685ef8392f246ebdba783ed03"):
        (source / ".git").mkdir(parents=True)
        (source / ".revision").write_text(revision)
        (source / "go.mod").write_text("module fixture\n\ngo 1.27.1\n")
        (source / "local-change.go").write_text("existing local patch\n")

    def run(*args):
        result = subprocess.run(["sh", str(scripts / "install_maps.sh"), *args],
                                capture_output=True, text=True, env=env)
        log = Path(env["INSTALL_EVENTS"])
        events = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, events
    return root, source, env, prepare, run


def test_default_installer_bootstraps_pinned_source_modules_and_browser(installer_sandbox):
    root, source, _, _, run = installer_sandbox
    result, events = run()
    assert result.returncode == 0, result.stderr
    assert any(name == "git" and "fetch" in args and args[-1] == source.name.split("scraper-")[1]
               for name, args, _ in events)
    assert any(name == "go" and args == ["mod", "download"] for name, args, _ in events)
    assert any(name.startswith("google-maps-scraper") and install == "1" for name, _, install in events)
    assert (root / "tools/bin/google-maps-scraper").is_file()


@pytest.mark.parametrize("existing", [False, True])
def test_offline_installer_never_downloads_or_installs_browser(installer_sandbox, existing):
    root, source, _, prepare, run = installer_sandbox
    if existing:
        prepare()
    result, events = run("--offline")
    assert (result.returncode == 0) == existing, result.stderr
    assert not any(name == "git" and "fetch" in args for name, args, _ in events)
    assert not any(name == "go" and args == ["mod", "download"] for name, args, _ in events)
    assert not any(name.startswith("google-maps-scraper") and install == "1" for name, _, install in events)
    if existing:
        assert (source / "local-change.go").read_text() == "existing local patch\n"


def test_default_installer_rejects_wrong_existing_revision_without_fetch_or_checkout(installer_sandbox):
    _, source, _, prepare, run = installer_sandbox
    prepare("0" * 40)
    result, events = run()
    assert result.returncode != 0
    assert not any(name == "git" and ({"fetch", "checkout"} & set(args))
                   for name, args, _ in events)
    assert not any(name == "go" and args == ["mod", "download"] for name, args, _ in events)
    assert (source / ".revision").read_text() == "0" * 40
    assert (source / "local-change.go").read_text() == "existing local patch\n"


def test_default_installer_preserves_current_binary_when_browser_setup_fails(installer_sandbox):
    root, source, env, prepare, run = installer_sandbox
    prepare()
    binary = root / "tools/bin/google-maps-scraper"
    binary.parent.mkdir(parents=True)
    binary.write_text("previous verified binary")
    env["FAIL_BROWSER"] = "1"
    result, events = run()
    assert result.returncode != 0
    assert binary.read_text() == "previous verified binary"
    assert (source / "local-change.go").read_text() == "existing local patch\n"
    assert not any(name == "git" and ({"fetch", "checkout"} & set(args))
                   for name, args, _ in events)


@pytest.mark.parametrize("offline", [False, True])
def test_installer_acquires_missing_toolchain_only_in_bootstrap_mode(installer_sandbox, offline):
    _, _, env, prepare, run = installer_sandbox
    prepare()
    env["OLD_GO"] = "1"
    result, events = run(*(["--offline"] if offline else []))
    assert (result.returncode == 0) == (not offline), result.stderr
    acquired = any(name == "go" and args == ["env", "GOROOT"] for name, args, _ in events)
    assert acquired == (not offline)
