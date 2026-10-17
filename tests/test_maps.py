"""Synthetic fixtures only; every subprocess/output lives under pytest tmp_path."""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest
from leadworkbench.sources import maps


ROW = dict(input_id="query-not-listing", link="https://www.google.com/maps?cid=123",
           title="Synthetic Fixture", address="Fixture address", phone="fixture-phone",
           website="https://example.invalid", latitude=34.0, longitude=-118.0,
           category="Fixture category", cid="123")


def test_upstream_web_site_field():
    row = {**ROW, "web_site": "https://official.example.invalid/"}
    row.pop("website")
    assert maps._record(row, "timestamp")["website"] == row["web_site"]


@pytest.mark.asyncio
async def test_deep_search_uses_adapter_timeout_not_upstream_zero_activity_timer(tmp_path, monkeypatch):
    # Upstream initializes lastActivityAt to the zero time until the first job
    # finishes; enabling its timer aborts long-running initial search jobs.
    fake(tmp_path, monkeypatch, "out.write_text(json.dumps(" + repr(ROW) + ")+'\\n')\n")
    await maps.discover_maps("fixture", tmp_path / "output", depth=10)
    metadata = json.loads(next((tmp_path / "output").glob("*/metadata.json")).read_text())
    command = metadata["command"]
    assert command[command.index("-exit-on-inactivity") + 1] == "0"


def fake(tmp_path, monkeypatch, body):
    executable = tmp_path / "fake-scraper"
    executable.write_text(f"#!{sys.executable}\nimport sys, json, time, os, subprocess, signal\n"
                          "from pathlib import Path\n"
                          "out=Path(sys.argv[sys.argv.index('-results')+1])\n" + body)
    executable.chmod(0o755)
    monkeypatch.setattr(maps, "BINARY", executable)


def test_schema_and_cid_normalization():
    record = maps._record(ROW, "timestamp")
    assert record == dict(source_key="google_maps:cid:123", source_url=ROW["link"],
                         name=ROW["title"], address=ROW["address"], phone=ROW["phone"],
                         website=ROW["website"], latitude=34.0, longitude=-118.0,
                         category=ROW["category"], observed_at="timestamp", operating_status="", status_evidence="")
    assert maps._record({**ROW, "cid": "", "link": "https://www.google.com/maps/place/Fixture", "data_id": "0x1:0x7b"}, "t")["source_key"] == record["source_key"]
    assert maps._record({**ROW, "cid": "0x7b"}, "t")["source_key"] == record["source_key"]


def test_identifier_fallbacks_and_provenance():
    row = {**ROW, "cid": "", "link": "https://www.google.com/maps/place/Fixture", "place_id": "ChIJfixture"}
    assert maps._record(row, "t")["source_key"] == "google_maps:place_id:ChIJfixture"
    a = maps._record({**row, "place_id": "", "link": "https://www.google.com/maps?q=A"}, "t")
    b = maps._record({**row, "place_id": "", "link": "https://www.google.com/maps?q=B"}, "t")
    assert a["source_key"] != b["source_key"]


@pytest.mark.parametrize("change", [{"link": "https://evil.example/maps"}, {"title": ""}, {"latitude": float("nan")}, {"longitude": 181}])
def test_reject_invalid_listing(change):
    with pytest.raises(ValueError):
        maps._record({**ROW, **change}, "t")


async def test_missing_binary_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(maps, "BINARY", tmp_path / "missing")
    result = await maps.discover_maps("clothing wholesaler Los Angeles", tmp_path)
    assert result["status"] == "blocked"
    assert result["stop_reason"] == "missing_binary"
    assert result["records"] == []
    metadata = json.loads(next(tmp_path.glob("maps-*/metadata.json")).read_text())
    assert metadata["query"] == "clothing wholesaler Los Angeles"
    assert metadata["returncode"] is None


async def test_valid_and_deduplicated(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, f"out.write_text({(json.dumps(ROW) + chr(10) + json.dumps(ROW))!r})\n")
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["status"] == "completed"
    assert len(result["records"]) == 1


async def test_nonzero_with_partial_records(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, f"out.write_text({json.dumps(ROW)!r}); sys.exit(7)\n")
    result = await maps.discover_maps("fixture", tmp_path)
    assert (result["status"], result["stop_reason"]) == ("partial", "nonzero_exit")
    assert len(result["records"]) == 1


@pytest.mark.parametrize("content,reason", [("not-json\n", "invalid_output"), ("", "empty_output")])
async def test_invalid_or_empty(tmp_path, monkeypatch, content, reason):
    fake(tmp_path, monkeypatch, f"out.write_text({content!r})\n")
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["status"] != "completed"
    assert result["stop_reason"] == reason


async def test_incremental_limit_and_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LEADSDB_API_KEY", "must-not-be-forwarded")
    fake(tmp_path, monkeypatch,
         "assert os.environ['DISABLE_TELEMETRY']=='1'\n"
         "assert 'LEADSDB_API_KEY' not in os.environ\n"
         "assert 'PLAYWRIGHT_DRIVER_PATH' in os.environ\n"
         f"out.write_text({(json.dumps(ROW) + chr(10))!r})\n"
         "time.sleep(30)\n")
    result = await asyncio.wait_for(maps.discover_maps("fixture", tmp_path, limit=1), 3)
    assert result["stop_reason"] == "company_limit"
    assert len(result["records"]) == 1


async def test_explicit_executable_configuration(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, f"out.write_text({json.dumps(ROW)!r})\n")
    actual = maps.BINARY
    monkeypatch.setenv("MAPS_SCRAPER_BIN", str(actual))
    monkeypatch.setattr(maps, "BINARY", tmp_path / "missing")
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["status"] == "completed"


async def test_timeout_kills_descendant(tmp_path, monkeypatch):
    marker = tmp_path / "surviving-child"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch()"
    fake(tmp_path, monkeypatch, f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)\n")
    result = await maps.discover_maps("fixture", tmp_path, timeout_seconds=0.2)
    assert result["stop_reason"] == "timeout"
    await asyncio.sleep(1)
    assert not marker.exists()


async def test_callback_stop(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "time.sleep(30)\n")
    calls = 0
    def stop():
        nonlocal calls
        calls += 1
        return calls >= 3
    result = await asyncio.wait_for(maps.discover_maps("fixture", tmp_path, should_stop=stop), 3)
    assert result["stop_reason"] == "cancelled"


async def test_task_cancellation_metadata(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "time.sleep(30)\n")
    task = asyncio.create_task(maps.discover_maps("fixture", tmp_path))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    metadata = json.loads(next(tmp_path.glob("maps-*/metadata.json")).read_text())
    assert metadata["stop_reason"] == "cancelled"
    assert metadata["returncode"] is not None


@pytest.mark.parametrize("query", ["", "a\nb", "a#!#b"])
async def test_reject_query_injection(tmp_path, query):
    with pytest.raises(ValueError):
        await maps.discover_maps(query, tmp_path)


async def test_limit_cleanup_is_once_and_keeps_metadata(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, f"out.write_text({(json.dumps(ROW) + chr(10))!r}); time.sleep(30)\n")
    terminate = maps._terminate
    calls = 0
    async def macos_cleanup(process):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise PermissionError("macOS killpg EPERM after successful cleanup")
        await terminate(process)
    monkeypatch.setattr(maps, "_terminate", macos_cleanup)
    result = await maps.discover_maps("fixture", tmp_path, limit=1)
    assert result["status"] == "completed"
    assert result["stop_reason"] == "company_limit"
    assert len(result["records"]) == 1
    assert calls == 1
    metadata = json.loads(next(tmp_path.glob("maps-*/metadata.json")).read_text())
    assert metadata["records_count"] == 1
    assert metadata["returncode"] is not None


@pytest.mark.parametrize("cancel", [False, True])
async def test_cleanup_error_still_persists_metadata(tmp_path, monkeypatch, cancel):
    fake(tmp_path, monkeypatch, "time.sleep(30)\n")
    terminate = maps._terminate
    async def failing_cleanup(process):
        await terminate(process)  # Do not leak an actual test process.
        raise PermissionError("simulated cleanup EPERM")
    monkeypatch.setattr(maps, "_terminate", failing_cleanup)
    task = asyncio.create_task(maps.discover_maps("fixture", tmp_path, timeout_seconds=0.15))
    if cancel:
        await asyncio.sleep(0.1)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else PermissionError):
        await task
    metadata = json.loads(next(tmp_path.glob("maps-*/metadata.json")).read_text())
    assert metadata["stop_reason"] == ("cancelled" if cancel else "adapter_error")
    assert metadata["returncode"] is not None
    assert any("cleanup" in warning.lower() for warning in metadata["warnings"])


async def test_market_cli_options_and_saturation(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch,
         "assert sys.argv[sys.argv.index('-geo')+1] == '34.1,-118.3'\n"
         "assert sys.argv[sys.argv.index('-zoom')+1] == '14'\n"
         "assert sys.argv[sys.argv.index('-depth')+1] == '10'\n"
         f"out.write_text({(json.dumps(ROW) + chr(10))!r}); time.sleep(30)\n")
    result = await maps.discover_maps('fixture', tmp_path, limit=1, geo=[34.1, -118.3], zoom=14, depth=10)
    assert result['saturated'] is True and result['exhausted'] is False
    assert result['stop_reason'] == 'company_limit'
    metadata = json.loads(next(tmp_path.glob('maps-*/metadata.json')).read_text())
    assert metadata['geo'] == [34.1, -118.3] and metadata['depth'] == 10
    assert metadata['saturated'] is True


async def test_saturation_when_process_exits_at_cap(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, f"out.write_text({json.dumps(ROW)!r})\n")
    result = await maps.discover_maps('fixture', tmp_path, limit=1)
    assert result['saturated'] is True and result['exhausted'] is False
    assert result['stop_reason'] == 'company_limit'


async def test_exhausted_only_for_success_below_cap(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, f"out.write_text({json.dumps(ROW)!r})\n")
    result = await maps.discover_maps('fixture', tmp_path)
    assert result['exhausted'] is True and result['saturated'] is False
    fake(tmp_path, monkeypatch, f"out.write_text({json.dumps(ROW)!r}); sys.exit(7)\n")
    result = await maps.discover_maps('fixture', tmp_path)
    assert result['exhausted'] is False


@pytest.mark.parametrize('options', [{'geo': [91, 0]}, {'geo': [0, float('nan')]},
                                    {'geo': [True, 1]}, {'geo': [1]}, {'zoom': 22},
                                    {'depth': 0}, {'depth': True}])
async def test_invalid_market_cli_options(tmp_path, options):
    with pytest.raises(ValueError):
        await maps.discover_maps('fixture', tmp_path, **options)
