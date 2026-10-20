import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { EXPORT_BUSY, EXPORT_DISABLED, ExportJobsPanel, exportBody, useExportJobs } from "../ExportJobs";
import { batchStatusPayload } from "../companyEdits";
import { CompanyStatus, StatusOptions } from "../CompanyStatus";
import { date, filtersQuery, hintLabel, tierClass, TIERS, type Filters } from "../helpers";
import { CategoryFilter } from "../CategoryFilter";
import { ExclusionRules } from "../ExclusionRules";
import { nextFrame, useResource } from "../hooks";
import { useMediaQuery } from "../useMediaQuery";
import { CompanyCards } from "../CompanyCards";
import { defaultFilters } from "../navigation";
import type { Company, CompanyPage, Task, CityCount } from "../types";
import { ActualCityCell } from "../ActualCity";
import { RECHECK_LIMIT, recheckConfirm, recheckQueued, type RecheckCreated } from "../MarketRecheck";
import { Alert, Badge, Empty, Icon, Modal } from "../ui";

import { Resource } from "../Resource";
import type { Action } from "./pageTypes";
export function CategoryTags({ categories }: { categories: string[] }) {
  if (!categories.length) return <span className="muted">—</span>;
  return (
    <div className="tags" title={categories.join("、")}>
      {categories.slice(0, 2).map((category) => (
        <span key={category} className="badge">{category}</span>
      ))}
      {categories.length > 2 && <span className="badge">+{categories.length - 2}</span>}
    </div>
  );
}
export function ExcludedNotice({
  hidden,
  mode,
  onChange,
}: {
  hidden: number;
  mode: "" | "only" | "include";
  onChange: (mode: "" | "only" | "include") => void;
}) {
  if (mode)
    return (
      <div className="alert excluded-notice" role="status">
        {mode === "only" ? "当前只显示按排除规则隐藏的企业" : "当前显示全部企业，含按排除规则隐藏的企业"} ·{" "}
        <button className="text-button" onClick={() => onChange("")}>恢复默认隐藏</button>
      </div>
    );
  if (!hidden) return null;
  return (
    <div className="alert excluded-notice" role="status">
      已按排除规则隐藏 {hidden} 家 ·{" "}
      <button className="text-button" onClick={() => onChange("only")}>查看已排除</button>{" "}/{" "}
      <button className="text-button" onClick={() => onChange("include")}>全部显示</button>
    </div>
  );
}
export const EXPORT_FOOTNOTE =
  "导出当前筛选下的全部结果，不受分页限制。联系入口表一行一个入口，已排除拒绝联系企业、不采用的联系人和按排除规则隐藏的企业；XLSX 另含企业、地点和来源证据表，每张表都带企业名称。";
const EXPORTS: [string, string, "csv" | "xlsx", "entries" | "companies", string][] = [
  ["entries-csv", "导出联系入口 CSV", "csv", "entries", "联系入口"],
  ["entries-xlsx", "导出联系入口 XLSX", "xlsx", "entries", "联系入口"],
  ["companies-csv", "导出企业表 CSV", "csv", "companies", "企业表"],
];
/** 筛选（n）：除 q 和 sort 以外非空的筛选项数；category、excluded 各算 1（review NIT11）。 */
export function activeFilterCount(filters: Filters): number {
  const keys = ["status", "type", "market_id", "run_id", "segment", "contact", "best_tier", "actual_city", "nearby"] as const;
  return keys.filter((k) => !!filters[k]).length + (filters.category?.length ? 1 : 0) + (filters.excluded ? 1 : 0);
}
/** 联系入口 column: best tier, deduplicated entry count, gap reason; legacy counts stay in the tooltip. */
export function EntryCell({ company: c }: { company: Company }) {
  return (
    <>
      <span className={tierClass(c.best_tier_rank)}>{c.best_tier_label || "—"}</span>
      <div
        title={`采购 ${c.procurement_count ?? "—"}（有明确采购岗位证据，按邮箱去重）· 其他邮箱 ${c.email_count ?? "—"} · 其他社媒 ${c.social_count ?? "—"}`}
      >
        联系入口 {c.contact_count} 个（已去重）
      </div>
      {c.gap && <small className="muted gap-reason">{c.gap.reason}</small>}
    </>
  );
}
export function CompaniesPage({
  version,
  filters,
  page,
  busy,
  act,
  refresh,
  updateFilters,
  setPage,
  openCompany,
  quickStatus,
  exportDays,
}: {
  version: number;
  filters: Filters;
  page: number;
  busy: boolean;
  act: Action;
  refresh: () => void;
  updateFilters: (patch: Partial<Filters>) => void;
  setPage: (update: (page: number) => number) => void;
  openCompany: (id: string) => void;
  quickStatus: (company: Company, status: string) => void;
  exportDays?: number;
}) {
  const tasks = useResource<Task[]>("/tasks?archived=include", version, true);
  const cities = useResource<CityCount[]>(
    `/company-cities?${new URLSearchParams({ market_id: filters.market_id || "", excluded: "hide" })}`,
    version,
  );
  const companies = useResource<CompanyPage>(
    `/company-page?${filtersQuery(filters, page)}`,
    version,
    true,
  );
  const [draftQuery, setDraftQuery] = useState(filters.q);
  const [selection, setSelection] = useState<
    Record<string, { id: string; revision: number }>
  >({});
  const [batchStatus, setBatchStatus] = useState("candidate");
  const [showRules, setShowRules] = useState(false);
  const exports = useExportJobs();
  useEffect(() => {
    setDraftQuery(filters.q);
    setSelection({});
  }, [filtersQuery(filters), page]);
  useEffect(() => {
    if (!companies.data) return;
    const visible = new Set(companies.data.items.map((c) => c.id));
    setSelection((previous) =>
      Object.fromEntries(
        Object.entries(previous).filter(([id]) => visible.has(id)),
      ),
    );
  }, [companies.data]);
  // A mutation/refetch invalidates captured revisions, including after a conflict.
  useEffect(() => {
    setSelection({});
  }, [version]);
  const narrow = useMediaQuery("(max-width: 779px)");
  const [sheet, setSheet] = useState(false);
  const [showStats, setShowStats] = useState(false);
  const exportMenu = useRef<HTMLDetailsElement>(null);
  // Drawer count keeps the last successful total while the list reloads (design §4.4).
  const lastTotal = useRef<number | undefined>(undefined);
  if (companies.data) lastTotal.current = companies.data.total;
  const pages = companies.data ? Math.max(1, Math.ceil(companies.data.total / companies.data.page_size)) : undefined;
  const activeFilters = activeFilterCount(filters);
  // U-12: any search or filter (sort does not count) means an empty result is “no match”, not an empty library.
  const filtered = Object.entries(filters).some(([key, value]) => key !== "sort" && (Array.isArray(value) ? value.length > 0 : Boolean(value)));
  function clearFilters() {
    setDraftQuery("");
    updateFilters(defaultFilters());
  }
  const searchForm = (
    <form
      className="search-box"
      onSubmit={(e) => {
        e.preventDefault();
        updateFilters({ q: draftQuery });
      }}
    >
      <Icon name="search" size={18} />
      <input
        aria-label="搜索企业"
        placeholder="搜索企业名称、地址、地区或官网"
        value={draftQuery}
        onChange={(e) => setDraftQuery(e.target.value)}
      />
      <button type="submit">搜索</button>
    </form>
  );
  const statsButton = (
    <button type="button" className="text-button stats-toggle" aria-expanded={showStats} aria-controls="stats-note"
      onClick={() => setShowStats((v) => !v)}>ⓘ 新增、存量与资料变化的统计口径</button>
  );
  const statsText = (
    <p id="stats-note" className="list-help">新增＝本轮首次进入该市场；存量＝此前已有且本轮再次找到；变化为其中资料发生变化的企业。未再次找到不代表停业，候选企业仍需业务核实。</p>
  );
  const exportButtons = (after?: () => void) => EXPORTS.map(([key, title, format, layout]) => (
    <button
      className={key === "entries-csv" ? "primary" : undefined}
      disabled={busy || !!exports.active || companies.loading || !!companies.error}
      key={key}
      onClick={() => {
        after?.();
        act(
          () => exports.start(exportBody(filters, format, layout)),
          (started) => (started ? "导出已在后台开始，完成后可在下方下载" : EXPORT_BUSY),
        );
      }}
    >
      <Icon name="download" size={16} />
      {title}
    </button>
  ));
  const filterFieldList = [
    { key: "market", label: "目标市场", control: (
        <select
          aria-label="目标市场筛选"
          value={filters.market_id || ""}
          onChange={(e) =>
            updateFilters({
              market_id: e.target.value,
              run_id: "",
              segment: "",
            })
          }
        >
          <option value="">全部市场</option>
          {tasks.data?.map((task) => (
            <option key={task.id} value={task.id}>
              {task.name}{task.archived_at ? "（已归档）" : ""}
            </option>
          ))}
        </select>
    ) },
    { key: "status", label: "客户匹配状态", control: (
        <select
          aria-label="客户匹配状态筛选"
          value={filters.status}
          onChange={(e) => updateFilters({ status: e.target.value })}
        >
          <option value="">全部客户匹配状态</option>
          <option value="candidate">候选企业</option>
          <option value="verified">符合目标客户</option>
          <option value="not_fit">不符合目标客户</option>
          <option value="do_not_contact">拒绝联系</option>
        </select>
    ) },
    { key: "tier", label: "最佳入口等级", control: (
        <select
          aria-label="最佳入口等级筛选"
          value={filters.best_tier || ""}
          onChange={(e) => updateFilters({ best_tier: e.target.value })}
        >
          <option value="">全部入口等级</option>
          {TIERS.map(([code, title]) => (
            <option key={code} value={code}>{title}</option>
          ))}
        </select>
    ) },
    { key: "segment", label: "本轮分类", control: (
        <select
          aria-label="本轮分类筛选"
          value={filters.segment || ""}
          disabled={!filters.market_id}
          onChange={(e) => updateFilters({ segment: e.target.value })}
        >
          <option value="">全部分类</option>
          <option value="new">本轮新增</option>
          <option value="existing">存量企业</option>
          <option value="changed">资料变化</option>
          <option value="not_seen">未再次找到</option>
        </select>
    ) },
    { key: "type", label: "企业类型", control: (
        <select
          aria-label="企业类型筛选"
          value={filters.type}
          onChange={(e) => updateFilters({ type: e.target.value })}
        >
          <option value="">全部类型</option>
          <option value="wholesale">批发商</option>
          <option value="retail">零售商</option>
          <option value="import">进口贸易商</option>
        </select>
    ) },
    { key: "city", label: "实际城市", control: (
        <select
          aria-label="实际城市筛选"
          value={filters.actual_city || ""}
          onChange={(e) => updateFilters({ actual_city: e.target.value })}
        >
          <option value="">全部城市</option>
          {cities.data?.map((city) => (
            <option key={city.city_key} value={city.city}>
              {city.city}（{city.count} 家）{city.nearby ? " · 邻近" : ""}
            </option>
          ))}
        </select>
    ) },
    { key: "nearby", label: "邻近城市", control: (
        <select
          aria-label="邻近城市筛选"
          value={filters.nearby || ""}
          onChange={(e) => updateFilters({ nearby: e.target.value as Filters["nearby"] })}
        >
          <option value="">全部</option>
          <option value="only">只看邻近城市</option>
          <option value="hide">隐藏邻近城市</option>
        </select>
    ) },
    { key: "contact", label: "联系入口", control: (
        <select
          aria-label="联系入口筛选"
          value={filters.contact || ""}
          onChange={(e) =>
            updateFilters({ contact: e.target.value as Filters["contact"] })
          }
        >
          <option value="">全部联系入口</option>
          <option value="procurement">有采购联系人</option>
          <option value="email">有其他公开邮箱</option>
        </select>
    ) },
    { key: "sort", label: "排序", control: (
        <select
          aria-label="企业排序"
          value={filters.sort || "newest"}
          onChange={(e) =>
            updateFilters({ sort: e.target.value as Filters["sort"] })
          }
        >
          <option value="newest">最新建档优先</option>
          <option value="procurement">采购联系人优先</option>
          <option value="email">其他邮箱优先</option>
          <option value="updated">最近更新优先</option>
          <option value="tier">入口等级优先</option>
        </select>
    ) },
  ];
  const filterFields = filterFieldList.map((f) => ({ ...f, field: <label className="field"><span>{f.label}</span>{f.control}</label> }));
  const desktopOrder = ["market", "segment", "status", "type", "contact", "tier", "sort", "city", "nearby"];
  return (
    <>
      {!narrow && <div className="panel toolbar market-toolbar">
        {searchForm}
        {desktopOrder.map((k) => filterFields.find((f) => f.key === k)!).map((f) => <span key={f.key} className="toolbar-field">{f.control}</span>)}
        <CategoryFilter
          marketId={filters.market_id}
          selected={filters.category || []}
          version={version}
          onChange={(category) => updateFilters({ category })}
        />
        <button aria-expanded={showRules} onClick={() => setShowRules((v) => !v)}>
          排除规则
        </button>
        {activeFilters > 0 && <button onClick={clearFilters}>清除筛选</button>}
      </div>}
      {narrow && <div className="panel toolbar narrow-toolbar">
        {searchForm}
        <button type="button" className="filter-button" onClick={() => setSheet(true)}>
          {activeFilters ? `筛选（${activeFilters}）` : "筛选"}
        </button>
      </div>}
      {sheet && (
        <Modal title="筛选企业" variant="sheet" onClose={() => setSheet(false)}
          headerExtra={<span className="sheet-count muted" aria-live="polite">当前 {companies.error ? "—" : lastTotal.current ?? "—"} 家</span>}>
          <div className="form-body filter-sheet">
            <div className="filter-sheet-wide">{filterFields[0].field}</div>
            <div className="filter-sheet-grid">{filterFields.slice(1).map((f) => <span key={f.key}>{f.field}</span>)}</div>
            <CategoryFilter inline marketId={filters.market_id} selected={filters.category || []} version={version}
              onChange={(category) => updateFilters({ category })} />
            <button type="button" className="text-button" onClick={() => {
              setSheet(false);
              setShowRules(true);
              nextFrame(() => document.getElementById("exclusion-rules-title")?.focus());
            }}>管理排除规则 →</button>
            {statsButton}
            {showStats && statsText}
          </div>
          <footer>
            <button type="button" onClick={clearFilters}>清除筛选</button>
            <button type="button" className="primary" onClick={() => setSheet(false)}>查看 {companies.error ? "—" : lastTotal.current ?? "—"} 家</button>
          </footer>
        </Modal>
      )}
      {showRules && <ExclusionRules version={version} busy={busy} act={act} />}
      <ExcludedNotice
        hidden={companies.data?.excluded_total ?? 0}
        mode={filters.excluded || ""}
        onChange={(excluded) => updateFilters({ excluded })}
      />
      {tasks.error && (
        <Alert>
          市场列表暂不可用：{tasks.error}{" "}
          <button onClick={refresh}>重试</button>
        </Alert>
      )}
      <div className="section-title list-count">
        <div className="count-line">
          <span className="muted">
            {narrow
              ? `共 ${companies.data?.total ?? "—"} 家 · 第 ${companies.data?.page ?? page} / ${pages ?? "—"} 页`
              : `共 ${companies.data?.total ?? "—"} 家 · 每页 50 家`}
          </span>
          {!narrow && statsButton}
        </div>
        {narrow ? (
          <details className="export-menu" ref={exportMenu} onKeyDown={(e) => {
            if (e.key === "Escape" && exportMenu.current?.open) {
              e.preventDefault();
              exportMenu.current.open = false;
              exportMenu.current.querySelector("summary")?.focus();
            }
          }}>
            <summary>导出</summary>
            <div className="export-menu-items">{exportButtons(() => { if (exportMenu.current) exportMenu.current.open = false; })}</div>
          </details>
        ) : (
          <div className="actions">
            {exports.active && <span className="export-disabled">{EXPORT_DISABLED}</span>}
            {exportButtons()}
          </div>
        )}
      </div>
      {!narrow && showStats && statsText}
      {narrow && exports.active && <p className="export-disabled">{EXPORT_DISABLED}</p>}
      <ExportJobsPanel state={exports} exportDays={exportDays} />
      {!narrow && !!Object.keys(selection).length && <div className="batch-toolbar" aria-label="批量客户匹配状态">
        <span>已选 {Object.keys(selection).length} 家（仅当前页）</span>
        <label>
          批量标记为{" "}
          <select
            aria-label="批量客户匹配状态"
            value={batchStatus}
            disabled={busy}
            onChange={(e) => setBatchStatus(e.target.value)}
          >
            <StatusOptions />
          </select>
        </label>
        <button
          disabled={
            busy ||
            !Object.keys(selection).length ||
            companies.loading ||
            !!companies.error
          }
          onClick={() =>
            act(async () => {
              const result = await api<{ updated: number }>(
                "/companies/batch-status",
                "POST",
                batchStatusPayload(Object.values(selection), batchStatus),
              );
              setSelection({});
              return result;
            }, "所选企业的客户匹配状态已保存")
          }
        >
          应用到所选企业
        </button>
        <button
          disabled={!Object.keys(selection).length || busy}
          onClick={() => setSelection({})}
        >
          清除选择
        </button>
        {filters.market_id && <>
          <button
            disabled={busy || Object.keys(selection).length > RECHECK_LIMIT}
            onClick={() => {
              const ids = Object.keys(selection);
              if (!window.confirm(recheckConfirm(ids.length))) return;
              act(async () => {
                const result = await api<RecheckCreated>(
                  `/tasks/${encodeURIComponent(filters.market_id!)}/website-recheck`, "POST", { company_ids: ids });
                setSelection({});
                return result;
              }, (result) => recheckQueued(result as RecheckCreated));
            }}
          >
            复核所选企业官网
          </button>
          {Object.keys(selection).length > RECHECK_LIMIT && <span className="muted">一次最多 20 家，请减少选择</span>}
        </>}
      </div>}
      <a className="skip-link inline-skip" href="#company-list" onClick={(e) => {
        e.preventDefault();
        document.getElementById("company-list")?.focus();
      }}>跳到企业列表</a>
      <Resource {...companies} retry={refresh}>
        {narrow && companies.data?.items.length ? (
          <CompanyCards items={companies.data.items} marketId={filters.market_id} onOpen={openCompany} />
        ) : (
        <section className="panel table-wrap" id={narrow ? undefined : "company-list"} tabIndex={narrow ? undefined : -1}
          role="region" aria-label="企业列表">
          {companies.data?.items.length ? (
            <table>
              <thead>
                <tr>
                  <th>
<label className="check-hit">                    <input
                      type="checkbox"
                      aria-label="选择当前页全部企业"
                      disabled={busy}
                      checked={
                        !!companies.data?.items.length &&
                        companies.data.items.every((c) => !!selection[c.id])
                      }
                      onChange={(e) =>
                        setSelection(
                          e.target.checked
                            ? Object.fromEntries(
                                (companies.data?.items || []).map((c) => [
                                  c.id,
                                  { id: c.id, revision: c.revision },
                                ]),
                              )
                            : {},
                        )
                      }
                    /></label>
                  </th>
                  <th>企业 / 地区</th>
                  <th>业务类型</th>
                  <th>来源类别</th>
                  {filters.market_id && <th>市场分类</th>}
                  <th>客户匹配状态</th>
                  <th>联系入口</th>
                  {filters.market_id && <th>市场发现时间</th>}
                  <th>官网采集时间</th>
                  <th>人工确认时间</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {companies.data.items.map((c) => (
                  <tr key={c.id}>
                    <td>
<label className="check-hit">                      <input
                        type="checkbox"
                        aria-label={`选择 ${c.name}`}
                        disabled={busy}
                        checked={!!selection[c.id]}
                        onChange={(e) => {
                          const checked = e.target.checked;
                          setSelection((previous) => {
                            const next = { ...previous };
                            if (checked)
                              next[c.id] = { id: c.id, revision: c.revision };
                            else delete next[c.id];
                            return next;
                          });
                        }}
                      /></label>
                    </td>
                    <td>
                      <button
                        className="company-link"
                        onClick={() => openCompany(c.id)}
                      >
                        <span className="avatar" aria-hidden="true">{c.name.slice(0, 1)}</span>
                        <span>
                          <strong>{c.name}</strong>
                          <ActualCityCell company={c} />
                          {c.exclusion && (
                            <small className="badge badge-excluded">
                              已排除：{c.exclusion.kind === "category" ? "类别" : "关键词"} {c.exclusion.value}
                            </small>
                          )}
                        </span>
                      </button>
                    </td>
                    <td>
                      <div className="tags">
                        {c.business_types.map((t) => (
                          <Badge key={t} value={t} />
                        ))}
                        {[...new Set((c.type_hints || []).map((h) => h.business_type))].map((t) => (
                          <span key={t} className="badge badge-hint" title="来自市场定义的企业类型，尚未核实">
                            {hintLabel(t)}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <CategoryTags categories={c.categories || []} />
                    </td>
                    {filters.market_id && (
                      <td>
                        <div className="tags">
                          {c.market_segment ? (
                            <Badge value={c.market_segment} />
                          ) : (
                            <span className="muted">待分类</span>
                          )}
                          {c.market_changed &&
                            c.market_segment !== "changed" && (
                              <Badge value="changed" />
                            )}
                        </div>
                      </td>
                    )}
                    <td>
                      <CompanyStatus
                        company={c}
                        busy={busy}
                        onChange={quickStatus}
                      />
                      {c.operating_status &&
                        c.operating_status !== "unknown" && (
                          <Badge value={c.operating_status} />
                        )}
                    </td>
                    <td className="entry-cell">
                      <EntryCell company={c} />
                    </td>
                    {filters.market_id && (
                      <td className="market-dates">
                        <small>首次 {date(c.first_seen)}</small>
                        <small>最近 {date(c.last_seen)}</small>
                      </td>
                    )}
                    <td className="muted">{date(c.last_checked_at)}</td>
                    <td className="muted">{date(c.manual_checked_at)}</td>
                    <td>
                      <button
                        aria-label={`查看 ${c.name}`}
                        className="icon-button"
                        onClick={() => openCompany(c.id)}
                      >
                        <Icon name="arrow" size={18} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            filtered ? (
              <Empty title="没有匹配的企业" action={<button type="button" className="primary" onClick={clearFilters}>清除筛选</button>}>
                当前搜索或筛选下没有企业。
              </Empty>
            ) : (
              <Empty title="企业库还是空的">
                更新目标市场或人工录入企业后，资料会保存在这里。可调整筛选查看其他记录。
              </Empty>
            )
          )}
        </section>
        )}
      </Resource>
      <nav className="pagination" aria-label="企业列表分页">
        <span className="muted">
          第 {companies.data?.page ?? page} /{" "}
          {companies.data
            ? Math.max(
                1,
                Math.ceil(companies.data.total / companies.data.page_size),
              )
            : "—"}{" "}
          页 · 共 {companies.data?.total ?? "—"} 家
        </span>
        <div className="actions">
          <button
            disabled={companies.loading || page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            上一页
          </button>
          <button
            disabled={
              companies.loading ||
              !!companies.error ||
              !companies.data ||
              companies.data.page * companies.data.page_size >=
                companies.data.total
            }
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </div>
      </nav>
      <p className="footnote">
        {EXPORT_FOOTNOTE}
      </p>
    </>
  );
}
