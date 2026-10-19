"""Loopback-only API with per-start local token and same-origin checks."""
from datetime import datetime, timezone
import asyncio
import os
from pathlib import Path
import secrets
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Body, FastAPI, HTTPException, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
import json
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextvars import ContextVar
import logging
import re
import sys
import time

from starlette.routing import Match

from .db import Conflict, Store
from .errors import (INTERNAL, REQUEST_ID, Busy, DomainError, NotFound, classify_storage_error,
                     test_fault as fault_enabled)
from . import logging_setup
from .export import export_data
from .industry_presets import merge_keywords
from .sources.website import normalize_url
from .validation_messages import translate_errors
from .worker import worker_lock

ROOT = Path(__file__).resolve().parent.parent
RESTORE_LOCK_WAIT_SECONDS = 5.0
log = logging.getLogger(__name__)
CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")
UNKNOWN_ENDPOINT = "接口不存在（界面与服务版本可能不一致，请刷新页面）"
UNAVAILABLE_DETAIL = {
    "readonly_storage": "数据目录不可写，本地服务无法读写数据：{path}。请检查权限或磁盘后重新启动。",
    "disk_full": "磁盘空间不足，本地服务无法初始化数据库：{path}。请清理磁盘空间后重新启动。",
    "storage_unavailable": "本地数据库初始化失败，请在终端查看错误或导出诊断包后重新启动。",
}


# Per-request holder shared with child tasks, so http_request can log the error code.
RESPONSE_CODE: ContextVar[dict | None] = ContextVar("response_code", default=None)


def _note_code(code):
    holder = RESPONSE_CODE.get()
    if holder is not None:
        holder["code"] = code


def error_response(status, code, detail, headers=None):
    """Every non-2xx /api/* body: {detail, code, request_id} (design §5.4)."""
    _note_code(code)
    return JSONResponse({"detail": detail, "code": code, "request_id": REQUEST_ID.get()},
                        status_code=status, headers=headers)


def domain_response(exc: DomainError):
    headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, Busy) else None
    _note_code(exc.code)
    body = {"detail": str(exc), "code": exc.code, "request_id": REQUEST_ID.get(), **exc.extra}
    return JSONResponse(body, status_code=exc.status, headers=headers)


MEDIA_TYPES = {"csv": "text/csv; charset=utf-8",
               "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
ARRAY_BATCH = 500


def json_array(rows):
    """Large array responses: read everything first, serialise in batches that yield
    the GIL, then stream ready-made strings. The body generator never touches the
    store, so sending it after the gate is released cannot overlap a restore (§7.6)."""
    chunks = []
    for start in range(0, len(rows), ARRAY_BATCH):
        chunks.append(",".join(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                               for row in rows[start:start + ARRAY_BATCH]))
        time.sleep(0)
    return StreamingResponse(_join_array(chunks), media_type="application/json",
                             headers={"X-Total-Count": str(len(rows))})


def _join_array(chunks):
    yield "["
    for index, chunk in enumerate(chunks):
        yield ("," if index and chunk else "") + chunk
    yield "]"


def unavailable_state(exc, data_dir):
    """(code, detail) for a store that could not be opened at start-up (design §5.6)."""
    classified = classify_storage_error(exc, data_dir)
    code = classified.code if classified and classified.code in ("readonly_storage", "disk_full") else "storage_unavailable"
    return code, UNAVAILABLE_DETAIL[code].format(path=data_dir)
class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SocialSettingsInput(Input):
    searxng_url: str = Field(default="", max_length=300)

class SocialConnectionsInput(Input):
    reader: Literal["http", "browser"] = "http"
    instagram_reader: Literal["browser", "instaloader"] = "browser"
    proxy_url: str = Field(default="", max_length=300)
    max_posts: int = Field(default=3, ge=1, le=5)
    youtube_api_key: str | None = Field(default=None, max_length=300)

    @field_validator("proxy_url")
    @classmethod
    def proxy_valid(cls, value):
        from .social_connections import validate_proxy
        return validate_proxy(value)


class SocialRunInput(Input):
    company_ids: list[str] = Field(default_factory=list, max_length=20)
    pilot: bool = False
    read_only: bool = False
    platform_search: bool = False
    profile_only: bool = False


class SocialLinkInput(Input):
    url: str = Field(min_length=1, max_length=4096)
    evidence: str = Field(default="", max_length=3000)


class SocialDecisionInput(Input):
    status: Literal["confirmed", "candidate", "excluded"]
    scope: Literal["brand", "location", "employee", "third_party", "unknown"] = "unknown"
    revision: int = Field(ge=1)


class SocialOutreachInput(Input):
    entity_id: str = Field(min_length=1, max_length=64)
    state: Literal["draft", "contacted", "replied", "closed"]
    note: str = Field(min_length=1, max_length=5000)


SocialPlatform = Literal["facebook", "instagram", "x"]
ReadableSocialPlatform = Literal["facebook", "instagram", "x", "tiktok", "youtube", "pinterest", "bluesky", "searxng"]


class MarketSocialSelectionInput(Input):
    social_platforms: list[SocialPlatform] = Field(
        default_factory=lambda: ["facebook", "instagram", "x"], max_length=3)
    social_read_posts: bool = False

    @field_validator("social_platforms")
    @classmethod
    def platforms_valid(cls, value):
        return list(dict.fromkeys(value))


class TaskSocialSettingsInput(MarketSocialSelectionInput):
    auto_social: bool = False

    @model_validator(mode="after")
    def enabled_platforms_required(self):
        if self.auto_social and not self.social_platforms:
            raise ValueError("启用社媒检索时请至少选择一个受支持的平台")
        return self


class MarketSocialRunInput(MarketSocialSelectionInput):
    @model_validator(mode="after")
    def platforms_required(self):
        if not self.social_platforms:
            raise ValueError("请至少选择一个受支持的社媒平台")
        return self


KEYWORDS_REQUIRED = "请填写检索关键词，或从预设行业中选择一个"


def industry_valid(value):
    from .industry_presets import BY_ID, UNKNOWN
    if value is not None and value not in BY_ID:
        raise ValueError(UNKNOWN)
    return value or None


def keywords_required(value, info):
    """Blank/length checks, then "keywords or a preset" reported on the keywords field (design §8.2)."""
    if any(not k.strip() or len(k) > 160 for k in value):
        raise ValueError("关键词不能为空或过长")
    value = list(dict.fromkeys(k.strip() for k in value))
    if "industry" not in info.data:
        return value  # The industry itself is invalid; one error is enough.
    if not value and not info.data["industry"]:
        raise ValueError(KEYWORDS_REQUIRED)
    return value


def ensure_keyword_limit(keywords):
    """After preset expansion at most 30 keywords; raised as a 422 on body.keywords (D12)."""
    from .industry_presets import MAX_KEYWORDS, TOO_MANY
    from .market_store import normalized
    if len({normalized(k) for k in keywords if normalized(k)}) > MAX_KEYWORDS:
        raise RequestValidationError([{"type": "value_error", "loc": ("body", "keywords"), "msg": TOO_MANY,
                                       "input": None, "ctx": {"error": ValueError(TOO_MANY)}}])


class TaskInput(TaskSocialSettingsInput):
    mode: Literal["market"] = "market"
    # Compatibility only: display names are generated from search conditions.
    name: str | None = Field(default=None, max_length=120, exclude=True)
    country: str = "US"
    state: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=120)
    state_id: str | None = Field(default=None, max_length=40)
    county_id: str | None = Field(default=None, max_length=40)
    place_id: str | None = Field(default=None, max_length=40)
    county: str = Field(default="", max_length=120)
    # Declared before keywords: the keywords validator reads it from info.data.
    industry: str | None = Field(default=None, max_length=40)
    keywords: list[str] = Field(default_factory=list, max_length=30, validate_default=True)
    company_types: list[Literal["wholesale", "retail", "import"]] = Field(default_factory=list, max_length=8)
    # Accept older clients, but a market never uses a company-count cutoff.
    limit: int | None = Field(default=None, exclude=True, ge=1)
    auto_enrich: bool = True

    @field_validator("industry")
    @classmethod
    def industry_valid(cls, value):
        return industry_valid(value)

    @field_validator("keywords")
    @classmethod
    def keywords_valid(cls, value, info: ValidationInfo):
        return keywords_required(value, info)

    @field_validator("country")
    @classmethod
    def country_valid(cls, value):
        value = value.strip().upper()
        if len(value) != 2 or not value.isascii() or not value.isalpha():
            raise ValueError("请输入两个英文字母的国家代码")
        return value

    @model_validator(mode="after")
    def target_required(self):
        if self.industry:
            self.keywords = merge_keywords(self.keywords, self.industry, self.company_types)
        if not all(v.strip() for v in (self.city, self.state)):
            raise ValueError("州省和城市不能为空")
        self.city, self.state = (v.strip() for v in (self.city, self.state))
        return self


class MarketPatch(Input):
    """Keyword edit; regions cannot change (extra=forbid -> 422 on body.<field>, design §6.1).
    No preset expansion here: the Store expands with the market's stored company types (R3-1)."""
    industry: str | None = Field(default=None, max_length=40)
    keywords: list[str] = Field(default_factory=list, max_length=30, validate_default=True)

    @field_validator("industry")
    @classmethod
    def industry_valid(cls, value):
        return industry_valid(value)

    @field_validator("keywords")
    @classmethod
    def keywords_valid(cls, value, info: ValidationInfo):
        return keywords_required(value, info)


class MarketPreviewInput(MarketPatch):
    """Preview of an edit: region and company types come from the stored market (D11)."""
    market_id: str = Field(min_length=1, max_length=64)


def validated(model, raw):
    """Validate a raw JSON body like FastAPI does (422 with body-prefixed locations)."""
    from pydantic import ValidationError
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in exc.errors()]) from None


class CompanyInput(Input):
    name: str = Field(min_length=1, max_length=200)
    website: str = Field(min_length=1, max_length=4096)
    city: str = Field(default="", max_length=120)
    country: str = Field(default="US", max_length=80)

    @field_validator("website")
    @classmethod
    def website_valid(cls, value):
        try:
            return normalize_url(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


class CompanyPatch(Input):
    revision: int = Field(ge=1)
    fields: dict[str, str] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=10000)
    status: Literal["candidate", "verified", "not_fit", "do_not_contact"] | None = None
    business_types: list[str] | None = Field(default=None, max_length=20)

    @field_validator("business_types")
    @classmethod
    def business_types_valid(cls, value):
        if value is not None and any(not item.strip() or len(item) > 80 for item in value):
            raise ValueError("无效业务类型")
        return list(dict.fromkeys(item.strip() for item in value)) if value is not None else None

    @field_validator("fields")
    @classmethod
    def fields_valid(cls, value):
        if set(value) - {"name", "address", "phone", "website"} or any(len(v) > 4096 for v in value.values()):
            raise ValueError("无效字段")
        if "name" in value and not value["name"].strip():
            raise ValueError("名称不能为空")
        if value.get("website"):
            try:
                value["website"] = normalize_url(value["website"])
            except Exception as exc:
                raise ValueError(str(exc)) from exc
        return value


class CompanyRevision(Input):
    id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)


class BatchStatus(Input):
    companies: list[CompanyRevision] = Field(min_length=1, max_length=200)
    status: Literal["candidate", "verified", "not_fit", "do_not_contact"]


class ExportJobInput(Input):
    format: Literal["csv", "xlsx"]
    layout: Literal["companies", "entries"] = "companies"
    q: str = Field(default="", max_length=200)
    status: str = Field(default="", max_length=40)
    type: str = Field(default="", max_length=40)
    market_id: str = Field(default="", max_length=100)
    run_id: str = Field(default="", max_length=100)
    contact: Literal["", "email", "procurement"] = ""
    sort: Literal["newest", "procurement", "email", "updated", "tier"] = "newest"
    segment: Literal["", "new", "existing", "changed", "not_seen"] = ""
    category: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=20)
    excluded: Literal["hide", "only", "include"] = "hide"
    best_tier: Literal["", "named_procurement", "named_role", "named_unknown", "department_email", "general_email",
                       "form_phone", "company_phone", "none"] = ""
    actual_city: str = Field(default="", max_length=120)
    nearby: Literal["", "only", "hide"] = ""


class ScheduleInput(Input):
    task_id: str | None = None
    company_id: str | None = None
    kind: Literal["discovery", "refresh"]
    # None: 1 month for a market (discovery), 3 for a company re-check (refresh).
    interval_months: int | None = Field(default=None, ge=1, le=24)
    timezone: str = "Asia/Shanghai"
    enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("无效时区") from exc
        return value


class Decision(Input):
    decision: Literal["accept", "reject"]


class SchedulePatch(Input):
    enabled: bool | None = None
    interval_months: int | None = Field(default=None, ge=1, le=24)
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("无效时区") from exc
        return value

    @model_validator(mode="after")
    def something_changes(self):
        if self.enabled is None and self.interval_months is None and self.timezone is None:
            raise ValueError("请至少修改一项")
        return self


class Restore(Input):
    name: str = Field(min_length=1, max_length=200)


class RetentionPolicyInput(Input):
    maps_raw_days: int = Field(ge=7, le=365)
    auto_backup_keep: int = Field(ge=1, le=100)
    export_days: int = Field(ge=1, le=90)


class RetentionConfirm(Input):
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")


def backup_kind(name):
    """manual / auto (with reason) / other; legacy backup-* are manual (decision D3)."""
    match = re.fullmatch(r"auto-(pre-restore|pre-repair)-.+\.sqlite3", name)
    if match:
        return "auto", match.group(1)
    return ("manual" if name.startswith("backup-") else "other"), None


class ExclusionRuleInput(Input):
    scope: Literal["global", "market"]
    market_id: str | None = Field(default=None, min_length=1, max_length=100)
    kind: Literal["category", "keyword"]
    value: str = Field(min_length=1, max_length=200)

    @field_validator("value")
    @classmethod
    def value_valid(cls, value):
        value = value.strip()
        if not 1 <= len(value) <= 120 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("排除值须为 1–120 个字符，且不含控制字符")
        return value

    @model_validator(mode="after")
    def scope_valid(self):
        if self.scope == "global" and self.market_id is not None:
            raise ValueError("全局规则不能指定市场")
        if self.scope == "market" and not self.market_id:
            raise ValueError("市场规则须指定市场")
        return self


class ManualContactInput(Input):
    revision: int = Field(ge=1)
    name: str = Field(default="", max_length=120)
    role: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=40)
    url: str = Field(default="", max_length=2048)
    procurement: bool = False
    note: str = Field(default="", max_length=1000)

    @field_validator("name", "role", "note")
    @classmethod
    def stripped(cls, value):
        return value.strip()

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        from .contact_quality import invalid_email_reason
        from .sources.website import EMAIL
        value = value.strip()
        if value and (not EMAIL.fullmatch(value) or invalid_email_reason(value)):
            raise ValueError("邮箱格式无效")
        return value

    @field_validator("phone")
    @classmethod
    def phone_valid(cls, value):
        import re
        value = value.strip()
        digits = re.sub(r"\D", "", value)
        if value and (re.search(r"[^0-9+\s().-]", value) or not 7 <= len(digits) <= 15):
            raise ValueError("电话格式无效")
        return value

    @field_validator("url")
    @classmethod
    def url_valid(cls, value):
        value = value.strip()
        if not value:
            return value
        try:
            return normalize_url(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def channel_required(self):
        if not (self.email or self.phone or self.url):
            raise ValueError("请至少填写邮箱、电话或联系页面中的一项")
        return self


# Repeatable ?category= filter: up to 20 values of 1–120 characters, OR semantics.
CategoryFilter = Annotated[list[Annotated[str, Field(min_length=1, max_length=120)]], Query(max_length=20)]
Excluded = Literal["hide", "only", "include"]
BestTier = Literal["", "named_procurement", "named_role", "named_unknown", "department_email", "general_email",
                   "form_phone", "company_phone", "none"]


def create_app(data_dir=None, *, allowed_hosts=None):
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def lifespan(application):
        yield
        # Export children are stopped by SIGTERM here, never by a terminal SIGINT (§8.5).
        await asyncio.to_thread(application.state.export_jobs.shutdown)
        from .sources.social_browser import close_session
        for platform in ("instagram", "facebook", "x"):
            await asyncio.to_thread(close_session, application.state.data_dir, platform)
    app = FastAPI(title="本地销售线索工作台", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.data_dir = Path(data_dir or os.environ.get("LEAD_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
    app.state.unavailable = None
    try:
        if fault_enabled("migration_slow"):
            time.sleep(20)
        if fault_enabled("storage_unavailable"):
            from .migrations import MigrationError
            raise MigrationError("LEAD_TEST_FAULT=storage_unavailable")
        store = Store(app.state.data_dir)
    except Exception as exc:
        # Degraded mode: keep serving /api/session and explain the cause (§5.6).
        store = None
        app.state.unavailable = unavailable_state(exc, app.state.data_dir)
        log.error("store unavailable at start-up", exc_info=True,
                  extra={"event": "store_unavailable", "code": app.state.unavailable[0]})
        print(app.state.unavailable[1], file=sys.stderr)
    app.state.store = store
    # JSON line logs only when the data directory is usable (degraded mode: console only).
    logging_setup.configure("api", app.state.data_dir if store is not None else None)
    from .export_jobs import ExportJobs
    jobs = app.state.export_jobs = ExportJobs(app.state.data_dir)
    if store is not None:
        jobs.list(1)  # reap jobs left running by an earlier API instance (§8.6)
    token = secrets.token_urlsafe(40)
    logging_setup.hide_token(token)
    hosts = allowed_hosts or {"127.0.0.1", "localhost", "::1"}
    from .maintenance import MaintenanceGate
    gate = app.state.gate = MaintenanceGate()
    from . import retention
    summaries = app.state.retention_summary = retention.SummaryCache(app.state.data_dir, gate)
    # Fast requests regain the GIL sooner while a CPU-heavy one runs (§7.6, step 1).
    sys.setswitchinterval(0.001)
    def unhandled(exc):
        """Storage errors get their code; anything else is a 500 with a request id (§5.5)."""
        classified = classify_storage_error(exc, app.state.data_dir)
        if classified is not None:
            log.warning("storage error: %s", exc, exc_info=True,
                        extra={"event": "storage_error", "code": classified.code,
                               "sqlite_code": getattr(exc, "sqlite_errorcode", None),
                               "errno": getattr(exc, "errno", None)})
            return domain_response(classified)
        log.exception("unhandled", extra={"event": "unhandled", "request_id": REQUEST_ID.get()})
        return error_response(500, "internal", INTERNAL.format(request_id=REQUEST_ID.get()))

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        request_id = "req_" + secrets.token_hex(6)
        context = REQUEST_ID.set(request_id)
        holder = {}
        code_context = RESPONSE_CODE.set(holder)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await boundary(request, call_next)
            if request.url.path.startswith("/api/"):
                response.headers["X-Request-ID"] = request_id
                log_request(request, response, started, holder.get("code"))
        finally:
            RESPONSE_CODE.reset(code_context)
            REQUEST_ID.reset(context)
        return response
    def log_request(request, response, started, code):
        """``http_request``: writes, errors and slow requests only; never the query string (§6.2)."""
        duration = round((time.perf_counter() - started) * 1000)
        if request.method == "GET" and response.status_code < 400 and duration < 500:
            return
        route = "<unmatched>"
        for candidate in app.router.routes:
            if candidate.matches(request.scope)[0] == Match.FULL:
                route = getattr(candidate, "path", route)
                break
        fields = {"event": "http_request", "method": request.method, "route": route,
                  "status": response.status_code, "duration_ms": duration,
                  "query_keys": sorted(set(request.query_params.keys()))}
        if code:
            fields["code"] = code
        log.info("%s %s %s", request.method, route, response.status_code, extra=fields)

    async def boundary(request, call_next):
        host = request.url.hostname
        if host not in hosts:
            return error_response(403, "forbidden", "仅允许本机访问")
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return error_response(403, "forbidden", "拒绝跨来源请求")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return error_response(403, "forbidden", "拒绝跨站请求")
        api = request.url.path.startswith("/api/")
        if api and request.url.path != "/api/session":
            if not secrets.compare_digest(request.headers.get("x-local-token", ""), token):
                return error_response(401, "unauthorized", "缺少本地访问令牌")
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 1_000_000:
            return error_response(413, "payload_too_large", "请求过大")
        unavailable = app.state.unavailable
        if api and unavailable and request.url.path not in ("/api/session", "/api/diagnostics/bundle"):
            return error_response(503, unavailable[0], unavailable[1])
        # Requests run concurrently; only restoring a backup is exclusive (P1-1).
        # The worker additionally shares the operation file lock.
        path, method = request.url.path, request.method
        exempt = path == "/api/session" or (method == "POST" and path == "/api/social/probe")
        try:
            if api and not exempt:
                entry = gate.exclusive() if (method == "POST" and path == "/api/backups/restore") else gate.shared()
                async with entry:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        except DomainError as exc:
            response = domain_response(exc)
        except Exception as exc:
            response = unhandled(exc)
        if api and unavailable:
            response.headers["X-Lead-Unavailable"] = unavailable[0]
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_failed(request, exc):
        # User-correctable input: Chinese messages, no input/ctx echo, no log.
        return error_response(422, "validation", translate_errors(exc.errors()))

    @app.exception_handler(DomainError)
    async def domain_failed(request, exc):
        return domain_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_failed(request, exc):
        codes = {404: "unknown_endpoint", 405: "method_not_allowed", 413: "payload_too_large"}
        detail = UNKNOWN_ENDPOINT if exc.status_code == 404 else exc.detail
        code = codes.get(exc.status_code, "invalid" if exc.status_code < 500 else "internal")
        return error_response(exc.status_code, code, detail, getattr(exc, "headers", None))

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        # Transitional: untyped Chinese ValueErrors are user errors, English ones are bugs.
        if CJK.search(str(exc)):
            frame = exc.__traceback__
            while frame and frame.tb_next:
                frame = frame.tb_next
            where = f"{frame.tb_frame.f_code.co_filename}:{frame.tb_lineno}" if frame else ""
            log.info("untyped ValueError", extra={"event": "untyped_value_error", "where": where})
            return error_response(400, "invalid", str(exc))
        return unhandled(exc)

    @app.get("/api/session")
    def session():
        return {"token": token}

    @app.get("/api/overview")
    def overview():
        binary = Path(os.environ.get("MAPS_SCRAPER_BIN", str(ROOT / "tools/bin/google-maps-scraper")))
        available = binary.is_file() and os.access(binary, os.X_OK)
        store._refresh_facets()
        with store.connect() as con:
            counts = {key: con.execute(sql).fetchone()[0] for key, sql in {
                "companies": "SELECT count(*) FROM companies WHERE merged_into IS NULL",
                "contacts": "SELECT count(*) FROM contacts", "tasks": "SELECT count(*) FROM tasks",
                "pending_reviews": "SELECT count(*) FROM reviews WHERE status='pending'",
                "running_runs": "SELECT count(*) FROM runs WHERE status='running'",
                "queued_runs": "SELECT count(*) FROM runs WHERE status='queued'",
            }.items()}
        with store.connect() as con:
            counts["contacts"] = store.contact_count(con)
        from .worker import worker_health
        counts["worker"] = worker_health(store.directory)
        from .maps_policy import status as maps_status
        with store.connect() as con:
            counts['maps_policy'] = maps_status(con)
        # Cached; recomputed in a background thread, never on this request (§10.6).
        counts["retention"] = {**summaries.get(), "export_days": retention.export_days(app.state.data_dir)}
        counts["source_status"] = {
            "maps": {"available": available, "message": "本地程序已安装；可用性以实际运行结果为准" if available else "未安装本地 Maps 程序，请运行 scripts/install_maps.sh"},
            "website": {"available": True, "message": "公开静态网页提取；登录或动态页面可能需人工核验"},
        }
        return counts

    @app.post("/api/maps/resume")
    def resume_maps():
        from .maps_policy import resume, status
        with store.connect() as con:
            store._begin(con)
            try:
                resume(con)
            except ValueError as exc:
                raise Conflict(str(exc)) from exc
            return status(con)

    @app.get("/api/region-configs")
    def region_configs():
        from .regions import country_configs
        return [{key: value for key, value in config.items() if key != "dataset"}
                for config in country_configs()]

    @app.get("/api/regions")
    def regions(parent_id: str | None = None, kind: Literal["state", "county", "place"] | None = None,
                county_id: str | None = None):
        from .regions import regions as list_regions
        return list_regions(parent_id, kind, county_id)

    @app.get("/api/tasks")
    def tasks():
        return store.tasks()

    @app.post("/api/tasks")
    def create_task(body: TaskInput):
        ensure_keyword_limit(body.keywords)
        return store.create_market(body.model_dump())

    @app.post("/api/tasks/preview")
    def preview_task(body: Annotated[dict, Body()]):
        # Same validation and order as create; no writes and no network (design §8.2).
        # With market_id it previews an edit of that market instead (D11).
        if "market_id" in body:
            edit = validated(MarketPreviewInput, body)
            return store.preview_market_edit(edit.market_id, edit.keywords, edit.industry)
        task = validated(TaskInput, body)
        ensure_keyword_limit(task.keywords)
        return store.preview_market(task.model_dump())

    @app.patch("/api/tasks/{id}")
    def update_task(id: str, body: MarketPatch):
        return store.update_market_keywords(id, body.keywords, body.industry)

    @app.get("/api/industry-presets")
    def industry_presets():
        from .industry_presets import presets
        return presets()

    @app.patch("/api/tasks/{id}/social-settings")
    def task_social_settings(id: str, body: TaskSocialSettingsInput):
        # Read, merge and write in one transaction: concurrent partial edits must not lose fields.
        return store.patch_task_social_settings(id, body.model_dump(exclude_unset=True))
    @app.post("/api/tasks/{id}/social")
    def task_social_start(id: str, body: MarketSocialRunInput):
        return store.start_market_social(
            id, platforms=body.social_platforms, read_posts=body.social_read_posts)

    @app.post("/api/tasks/{id}/runs")
    def start(id: str):
        return store.start_task(id)

    @app.get("/api/tasks/{id}/activity")
    def task_activity(id: str, page: int = Query(1, ge=1),
                      status: Literal["", "pending", "running", "succeeded", "partial", "failed", "blocked"] = "", run_id: str = ""):
        return store.task_activity(id, page=page, status=status, run_id=run_id)

    @app.get("/api/refresh-activity")
    def refresh_activity(page: int = Query(1, ge=1),
                         status: Literal["", "pending", "running", "succeeded", "partial", "failed", "blocked"] = "", run_id: str = ""):
        return store.refresh_activity(page=page, status=status, run_id=run_id)

    @app.get("/api/runs/{id}/items/{item_id}")
    def refresh_detail(id: str, item_id: str):
        return store.refresh_detail(id, item_id)

    @app.get("/api/runs")
    def runs():
        return store.runs()

    @app.get("/api/runs/{id}")
    def run(id: str):
        return store.run(id)

    # Must stay registered before /api/runs/{id}/{action}, which would answer 422 for "relocate".
    @app.post("/api/runs/{id}/relocate")
    def relocate(id: str):
        return store.relocate_market_run(id)

    @app.post("/api/runs/{id}/{action}")
    def control(id: str, action: Literal["pause", "resume", "cancel"]):
        return store.control(id, action)

    @app.get("/api/companies")
    def companies(q: str = "", status: str = "", type: str = "", category: CategoryFilter = [],
                  excluded: Excluded = "hide", page: int | None = Query(default=None, ge=1),
                  page_size: int | None = Query(default=None, ge=1, le=200),
                  sort: Literal["newest", "procurement", "email", "updated", "tier"] = "newest"):
        # Without page/page_size: the deprecated full array; otherwise the /api/company-page envelope.
        if page is None and page_size is None:
            return json_array(store.companies(q, status, type, category=category, excluded=excluded, sort=sort))
        return store.company_page(q=q, status=status, type=type, sort=sort, page=page or 1, page_size=page_size or 50,
                                  category=category, excluded=excluded)
    @app.get("/api/company-page")
    def company_page(q: str = "", status: str = "", type: str = "", market_id: str = "",
                     run_id: str = "", segment: Literal["", "new", "existing", "changed", "not_seen"] = "",
                     contact: Literal["", "email", "procurement"] = "",
                     sort: Literal["newest", "procurement", "email", "updated", "tier"] = "newest",
                     page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
                     category: CategoryFilter = [], excluded: Excluded = "hide", best_tier: BestTier = "",
                     actual_city: str = Query(default="", max_length=120),
                     nearby: Literal["", "only", "hide"] = ""):
        return store.company_page(q=q, status=status, type=type, market_id=market_id,
                                  run_id=run_id, segment=segment, contact=contact, sort=sort, page=page, page_size=page_size,
                                  category=category, excluded=excluded, best_tier=best_tier,
                                  actual_city=actual_city, nearby=nearby)

    @app.get("/api/company-cities")
    def company_cities(market_id: str = "", excluded: Excluded = "hide"):
        return store.company_cities(market_id=market_id, excluded=excluded)

    @app.get("/api/company-categories")
    def company_categories(market_id: str = "", excluded: Excluded = "hide"):
        return store.company_categories(market_id=market_id, excluded=excluded)

    @app.get("/api/exclusion-rules")
    def exclusion_rules(market_id: str = ""):
        return store.exclusion_rules(market_id)

    @app.post("/api/exclusion-rules")
    def create_exclusion_rule(body: ExclusionRuleInput):
        rule = store.create_exclusion_rule(body.scope, body.kind, body.value, body.market_id)
        return JSONResponse(rule, status_code=200 if rule["reused"] else 201)

    @app.delete("/api/exclusion-rules/{id}", status_code=204)
    def delete_exclusion_rule(id: str):
        store.delete_exclusion_rule(id)
        return Response(status_code=204)

    @app.post("/api/companies")
    def create_company(body: CompanyInput):
        return store.manual_company(**body.model_dump())

    @app.post("/api/companies/batch-status")
    def batch_status(body: BatchStatus):
        return store.batch_status([c.model_dump() for c in body.companies], body.status)

    @app.get("/api/companies/{id}")
    def detail(id: str):
        return store.detail(id)

    @app.patch("/api/companies/{id}")
    def update_company(id: str, body: CompanyPatch):
        return store.update_company(id, **body.model_dump())

    @app.post("/api/companies/{id}/contacts")
    def add_manual_contact(id: str, body: ManualContactInput):
        return store.add_manual_contact(id, **body.model_dump())

    @app.post("/api/companies/{id}/refresh")
    def refresh(id: str):
        return store.refresh(id)

    @app.get("/api/social/settings")
    def social_settings():
        return store.social_settings()

    @app.get("/api/social/connections")
    def social_connections():
        from .social_connections import public_connections
        return public_connections(store.directory)

    @app.put("/api/social/connections")
    def social_connections_save(body: SocialConnectionsInput):
        from .social_connections import save_connections
        from .sources.social_browser import session_status
        if any(session_status(store.directory,p)["state"]=="open" for p in ("instagram","facebook","x")):
            raise Conflict("请先关闭登录窗口，再修改连接配置")
        return save_connections(store.directory, body.model_dump(exclude_unset=True, exclude_none=True))

    @app.post("/api/social/browser/{platform}/open")
    def social_browser_open(platform: Literal["instagram", "facebook", "x"]):
        from .sources.social_browser import open_session
        try:
            return open_session(store.directory, platform)
        except RuntimeError as exc:
            raise Conflict(str(exc)) from exc

    @app.post("/api/social/browser/{platform}/close")
    def social_browser_close(platform: Literal["instagram", "facebook", "x"]):
        from .sources.social_browser import close_session
        return close_session(store.directory, platform)

    @app.put("/api/social/settings")
    def social_settings_update(body: SocialSettingsInput):
        return store.set_social_settings(body.searxng_url)

    @app.post("/api/social/platforms/{platform}/resume")
    def social_platform_resume(platform: ReadableSocialPlatform):
        try:
            return store.resume_social_platform(platform)
        except ValueError as exc:
            raise Conflict(str(exc)) from exc

    @app.post("/api/social/probe")
    async def social_probe():
        from .sources.social_discovery import search_searx
        # Only the settings read enters the gate (in the thread pool); the network wait does not.
        async with gate.shared():
            settings = await run_in_threadpool(store.social_settings)
        result = await search_searx(settings["searxng_url"], '"apparel" site:instagram.com')
        return {"status": result["status"], "message": result["message"]}

    @app.post("/api/social/runs")
    def social_run_create(body: SocialRunInput):
        return store.create_social_run(body.company_ids, body.pilot, body.read_only, body.platform_search, body.profile_only)

    @app.get("/api/social/runs")
    def social_run_list(company_id: str = ""):
        return store.social_runs(company_id)

    @app.get("/api/social/runs/{id}")
    def social_run_detail(id: str):
        return store.social_run(id)

    @app.post("/api/social/runs/{id}/cancel")
    def social_run_cancel(id: str):
        return store.cancel_social_run(id)

    @app.post("/api/social/runs/{id}/retry")
    def social_run_retry(id: str):
        return store.retry_social_run(id)

    @app.get("/api/companies/{id}/social")
    def social_detail(id: str):
        return store.social_detail(id)

    @app.post("/api/companies/{id}/social/sync")
    def social_sync(id: str):
        return store.sync_social(id)

    @app.post("/api/companies/{id}/social/links")
    def social_add(id: str, body: SocialLinkInput):
        return store.add_social(id, **body.model_dump())

    @app.post("/api/companies/{id}/social/{entity_id}/decision")
    def social_decision(id: str, entity_id: str, body: SocialDecisionInput):
        return store.decide_social(id, entity_id, **body.model_dump())

    @app.post("/api/companies/{id}/social/outreach")
    def social_outreach(id: str, body: SocialOutreachInput):
        return store.record_social_outreach(id, body.entity_id, body.state, body.note)

    @app.get("/api/reviews")
    def reviews(page: int | None = Query(default=None, ge=1), page_size: int | None = Query(default=None, ge=1, le=200),
                kind: Literal["", "field_update", "contact", "duplicate_candidate"] = ""):
        # Without page/page_size: the deprecated full array (with X-Total-Count).
        if page is None and page_size is None:
            return json_array(store.reviews(kind=kind))
        return store.reviews(page=page, page_size=page_size, kind=kind)
    @app.post("/api/reviews/{id}/decision")
    def decision(id: str, body: Decision):
        result = store.review_decision(id, body.decision)
        if isinstance(result, dict):
            return result  # contact change: may carry kept_previous
        return {"ok": True, "auto_closed": result or 0}

    @app.get("/api/changes")
    def changes():
        return store.changes()

    @app.get("/api/schedules")
    def schedules(task_id: str | None = Query(default=None, min_length=1, max_length=64)):
        return store.schedules(task_id)

    @app.post("/api/schedules")
    def create_schedule(body: ScheduleInput):
        return store.create_schedule(**body.model_dump())

    @app.patch("/api/schedules/{id}")
    def update_schedule(id: str, body: SchedulePatch):
        return store.update_schedule(id, **body.model_dump())

    @app.get("/api/settings")
    def settings():
        if fault_enabled("internal"):
            raise RuntimeError("LEAD_TEST_FAULT=internal")
        return settings_payload()

    def settings_payload():
        return {"data_dir": str(app.state.data_dir), "version": "0.1.0", "network_mode": "direct-public",
                "paid_services": False, "ai_enabled": False}

    @app.get("/api/diagnostics/bundle")
    def diagnostics_bundle():
        # Shared gate (middleware), thread pool (sync route), in memory only; works in degraded mode (§6.4).
        from . import diagnostics
        content = diagnostics.build(app.state.data_dir, store, settings=settings_payload(), token=token)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return Response(content, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="diagnostics-{stamp}.zip"'})

    @app.get("/api/backups")
    def backups():
        result = []
        for p in sorted((store.directory / "backups").glob("*.sqlite3"), reverse=True):
            info = p.stat()
            kind, reason = backup_kind(p.name)
            result.append({"name": p.name, "created_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                           "kind": kind, "reason": reason, "size_bytes": info.st_size})
        return result

    @app.get("/api/retention")
    def retention_view():
        summaries.invalidate()
        return retention.view(app.state.data_dir)

    @app.put("/api/retention/policy")
    def retention_policy(body: RetentionPolicyInput):
        summaries.invalidate()
        return retention.set_policy(app.state.data_dir, body.model_dump())

    @app.post("/api/retention/confirm")
    def retention_confirm(body: RetentionConfirm):
        summaries.invalidate()
        result = retention.run(app.state.data_dir, "confirm", expected_plan=body.plan_id)
        summaries.invalidate()
        state = result.pop("state")
        return {**result, **retention.view(app.state.data_dir, state)}

    @app.post("/api/backups")
    def backup():
        return store.backup()

    @app.post("/api/backups/restore")
    def restore(body: Restore):
        # An idle worker releases worker.lock between ticks; wait briefly for it.
        # Paused runs deliberately do not block a restore (product decision).
        try:
            # Inside the exclusive gate: an export child must not read while the file is replaced.
            if jobs.any_live():
                raise DomainError("正在生成导出文件，请等待完成或取消后再恢复备份", code="export_running", status=409)
            with worker_lock(store.directory, wait_seconds=RESTORE_LOCK_WAIT_SECONDS,
                             message="后台正在处理作业，请等待当前作业完成或暂停相关任务后再恢复备份"):
                if store.active_runs():
                    raise Conflict("请先取消活动任务再恢复")
                with store.connect() as con:
                    if con.execute("SELECT 1 FROM social_runs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                        raise Conflict("请先取消活动社媒任务再恢复")
                store.restore(body.name)
        except RuntimeError as exc:
            raise Conflict(str(exc)) from exc
        return {"ok": True}

    @app.get("/api/exports")
    def export(format: Literal["csv", "xlsx"] = "csv", layout: Literal["companies", "entries"] = "companies",
               q: str = "", status: str = "", type: str = "",
               market_id: str = "", run_id: str = "",
               contact: Literal["", "email", "procurement"] = "",
               sort: Literal["newest", "procurement", "email", "updated", "tier"] = "newest",
               segment: Literal["", "new", "existing", "changed", "not_seen"] = "",
               category: CategoryFilter = [], excluded: Excluded = "hide", best_tier: BestTier = ""):
        output = export_data(store, format, layout, q=q, status=status, type=type,
                             market_id=market_id, run_id=run_id, segment=segment, contact=contact, sort=sort,
                             category=category, excluded=excluded, best_tier=best_tier)
        # Compatibility only: the UI uses /api/export-jobs.
        return Response(output, media_type=MEDIA_TYPES[format],
                        headers={"Content-Disposition": 'attachment; filename="'
                                 + ("contact-entries" if layout == "entries" else "leads") + f'.{format}"',
                                 "Deprecation": "true", "Link": '</api/export-jobs>; rel="successor-version"'})

    @app.post("/api/export-jobs", status_code=202)
    def export_job_create(body: ExportJobInput, request: Request):
        from .export import normalize_filters
        filters = body.model_dump(exclude={"format", "layout"})
        filters = normalize_filters(store, body.layout, filters)
        if filters.get("market_id"):
            with store.connect() as con:
                if not con.execute("SELECT 1 FROM tasks WHERE id=?", (filters["market_id"],)).fetchone():
                    raise NotFound("市场不存在")
        return jobs.create({"format": body.format, "layout": body.layout, "filters": filters},
                           request.state.request_id)

    @app.get("/api/export-jobs")
    def export_job_list(limit: int = Query(5, ge=1, le=20)):
        return jobs.list(limit)

    @app.get("/api/export-jobs/{id}")
    def export_job_detail(id: str):
        return jobs.get(id)

    @app.post("/api/export-jobs/{id}/cancel")
    def export_job_cancel(id: str):
        return jobs.cancel(id)

    @app.get("/api/export-jobs/{id}/file")
    def export_job_file(id: str):
        from .export_jobs import content_disposition
        path, job = jobs.file(id)
        return FileResponse(path, media_type=MEDIA_TYPES[job["format"]],
                            headers={"Content-Disposition": content_disposition(job)})
    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, UNKNOWN_ENDPOINT)
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        return Response("Frontend not built. Run: cd frontend && npm ci && npm run build", status_code=503)
    return app
