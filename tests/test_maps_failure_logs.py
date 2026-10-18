"""Sanitized, offline reproductions; subprocess artifacts stay in tmp_path."""
import json

import pytest

from leadworkbench import market_plan
from leadworkbench.sources import maps
from test_maps import ROW, fake
from test_market_plan import CONFIG


@pytest.fixture(autouse=True)
def no_catalogue_fallback(monkeypatch):
    """These tests cover the geocoder path; the P0-5 catalogue fallback is tested in test_market_relocate.py."""
    monkeypatch.setattr(market_plan, "_catalogue_fallback", lambda *args, **kwargs: None)


@pytest.mark.parametrize("error,reason", [
    ("playwright: target closed: Target page, context or browser has been closed", "browser_closed"),
    ("inactivity timeout: 0001-01-01T00:00:00Z", "inactivity_timeout"),
    ("page crashed", "browser_closed"),
    ("browser has disconnected", "browser_closed"),
    ("Target page, context or browser has been closed", "browser_closed"),
])
@pytest.mark.parametrize("with_records", [False, True])
async def test_info_error_prevents_false_exhaustion(tmp_path, monkeypatch, error, reason, with_records):
    event = json.dumps({"level": "info", "error": error, "message": "worker stopped"})
    fake(tmp_path, monkeypatch,
         f"out.write_text({json.dumps(ROW) if with_records else ''!r})\n"
         f"print({event!r}, file=sys.stderr)\n")
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["status"] == ("partial" if with_records else "blocked")
    assert result["stop_reason"] == reason
    assert result["exhausted"] is False
    assert len(result["records"]) == int(with_records)
    assert result["warnings"][0].startswith("采集程序异常：")
    saved = json.loads(next(tmp_path.glob("maps-*/metadata.json")).read_text())
    assert saved["returncode"] == 0
    assert saved["stop_reason"] == reason and saved["exhausted"] is False


@pytest.mark.parametrize("event", [
    {"level": "info", "numOfJobsFailed": 0},
    {"level": "info", "error": ""},
    {"level": "info", "message": "no inactivity timeout configured"},
])
async def test_informational_logs_preserve_true_exhaustion(tmp_path, monkeypatch, event):
    fake(tmp_path, monkeypatch,
         f"out.write_text({json.dumps(ROW)!r})\n"
         f"print({json.dumps(event)!r}, file=sys.stderr)\n")
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["status"] == "completed"
    assert result["stop_reason"] == "finished"
    assert result["exhausted"] is True
    assert not result["warnings"]


async def test_adapter_stop_keeps_priority_over_shutdown_error(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch,
         f"out.write_text({(json.dumps(ROW) + chr(10))!r})\n"
         'print(json.dumps({"level":"info","error":"playwright: target closed"}), file=sys.stderr, flush=True)\n'
         "time.sleep(30)\n")
    result = await maps.discover_maps("fixture", tmp_path, limit=1)
    assert result["stop_reason"] == "company_limit"
    assert result["saturated"] is True and result["exhausted"] is False


async def test_blank_launch_exception_retains_type(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "")
    async def fail(*args, **kwargs):
        raise OSError()
    monkeypatch.setattr(maps.asyncio, "create_subprocess_exec", fail)
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["status"] == "blocked" and result["stop_reason"] == "launch_failed"
    assert result["warnings"] == ["OSError"]


async def test_blank_adapter_exception_is_persisted(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "")
    async def fail(*args, **kwargs):
        raise ValueError()
    monkeypatch.setattr(maps.asyncio, "create_subprocess_exec", fail)
    with pytest.raises(ValueError):
        await maps.discover_maps("fixture", tmp_path)
    saved = json.loads(next(tmp_path.glob("maps-*/metadata.json")).read_text())
    assert saved["stop_reason"] == "adapter_error"
    assert any("ValueError" in warning for warning in saved["warnings"])


@pytest.mark.parametrize("error", [TimeoutError(), OSError(), ValueError()])
async def test_outer_city_cache_failure_preserves_type(tmp_path, monkeypatch, error):
    async def fail(*args):
        raise error
    monkeypatch.setattr(market_plan, "_geocode", fail)
    result = await market_plan.plan_market(CONFIG, tmp_path)
    assert result["status"] == "completed"
    assert result["mode"] == "text_only"
    assert result["coverage"]["bbox"] is None
    assert type(error).__name__ in result["warnings"][0]
    assert all(job["geo"] is None and job["level"] == 0 for job in result["jobs"])


@pytest.mark.parametrize('error,reason', [
    ('captcha required', 'captcha'),
    ('Google detected unusual traffic from your network', 'captcha'),
    ('HTTP 429 Too Many Requests', 'rate_limited'),
    ('HTTP 403 access denied', 'access_denied'),
])
@pytest.mark.parametrize('with_records',[False,True])
async def test_restriction_stops_live_child_and_preserves_results(tmp_path,monkeypatch,error,reason,with_records):
    event=json.dumps({'level':'error','error':error})
    fake(tmp_path,monkeypatch,
         f"out.write_text({json.dumps(ROW) if with_records else ''!r})\n"
         f"print({event!r},file=sys.stderr,flush=True)\n"
         "time.sleep(20)\n")
    result=await maps.discover_maps('fixture',tmp_path,timeout_seconds=4)
    assert result['status']=='blocked' and result['stop_reason']==reason
    assert len(result['records'])==int(with_records)
    assert not result['exhausted']


async def test_restriction_on_fast_nonzero_exit_is_not_generic_error(tmp_path,monkeypatch):
    fake(tmp_path,monkeypatch,
         "print(json.dumps({'error':'HTTP 429 Too Many Requests'}),file=sys.stderr)\n"
         "sys.exit(1)\n")
    result=await maps.discover_maps('fixture',tmp_path)
    assert result['stop_reason']=='rate_limited' and result['status']=='blocked'


@pytest.mark.parametrize('event',[
    {'title':'Captcha Clothing Store','query':'HTTP 403 fashion'},
    {'level':'info','message':'captcha checks enabled; rate limit configured'},
    {'level':'info','error':'no captcha detected'},
])
async def test_business_and_informational_text_does_not_trigger_restriction(tmp_path,monkeypatch,event):
    fake(tmp_path,monkeypatch,
         f"out.write_text({json.dumps(ROW)!r})\n"
         f"print({json.dumps(event)!r},file=sys.stderr)\n")
    result=await maps.discover_maps('fixture',tmp_path)
    assert result['status']=='completed'


@pytest.mark.parametrize("term", maps.CRASH_TERMS)
async def test_oversized_crash_line_is_browser_closed(tmp_path, monkeypatch, term):
    # Real crash errors embed full browser logs: lines of 150-320 KB.
    event = {"level": "info", "error": f"{term}: browser closed. Browser logs: " + "x" * 300_000,
             "message": "worker stopped"}
    fake(tmp_path, monkeypatch, f"print({json.dumps(event)!r}, file=sys.stderr)\n")
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["stop_reason"] == "browser_closed"
    assert result["status"] == "blocked"
    assert result["warnings"][0].startswith("采集程序异常：")


async def test_oversized_line_with_query_text_is_not_crash(tmp_path, monkeypatch):
    event = {"level": "info", "message": "query playwright: target closed " + "x" * 300_000}
    fake(tmp_path, monkeypatch, f"print({json.dumps(event)!r}, file=sys.stderr)\n")
    result = await maps.discover_maps("fixture", tmp_path)
    assert result["stop_reason"] == "empty_output"
    assert result["warnings"][0].startswith("没有找到结果：")


async def test_user_stop_warning_is_pause_not_scraper_error(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "time.sleep(20)\n")
    calls = []
    def should_stop():
        calls.append(1)
        return len(calls) > 1  # Launch check passes, then the user pauses.
    result = await maps.discover_maps("fixture", tmp_path, should_stop=should_stop)
    assert result["stop_reason"] == "cancelled"
    assert result["status"] == "partial"
    assert result["warnings"][0].startswith("已暂停：")
    assert "采集程序异常" not in result["warnings"][0]


async def test_time_budget_warning_is_scraper_error(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "time.sleep(20)\n")
    result = await maps.discover_maps("fixture", tmp_path, timeout_seconds=0.2)
    assert result["stop_reason"] == "timeout"
    assert result["warnings"][0] == "采集程序异常：单次查询达到 180 秒时间上限后停止，结果不完整"


async def test_final_output_read_failure_has_chinese_prefix(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "")
    async def fail(*args, **kwargs):
        raise ValueError("boom")
    monkeypatch.setattr(maps.asyncio, "create_subprocess_exec", fail)
    real_open = maps.Path.open
    def broken_open(self, *args, **kwargs):
        if self.name == "results.jsonl":
            raise OSError("disk")
        return real_open(self, *args, **kwargs)
    monkeypatch.setattr(maps.Path, "open", broken_open)
    monkeypatch.setattr(maps.Path, "exists", lambda self: True if self.name == "results.jsonl" else real_exists(self))
    with pytest.raises(ValueError):
        await maps.discover_maps("fixture", tmp_path)
    saved = json.loads(next(tmp_path.glob("maps-*/metadata.json")).read_text())
    assert "采集程序异常：Final output read failed: disk" in saved["warnings"]
    assert "采集程序异常：Maps adapter failed: boom" in saved["warnings"]


real_exists = maps.Path.exists
