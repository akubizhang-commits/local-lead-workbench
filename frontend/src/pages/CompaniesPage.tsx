import { useEffect, useState } from "react";
import { api, download } from "../api";
import { batchStatusPayload } from "../companyEdits";
import { CompanyStatus, StatusOptions } from "../CompanyStatus";
import { date, filtersQuery, hintLabel, type Filters } from "../helpers";
import { CategoryFilter } from "../CategoryFilter";
import { ExclusionRules } from "../ExclusionRules";
import { useResource } from "../hooks";
import { defaultFilters } from "../navigation";
import type { Company, CompanyPage, Task } from "../types";
import { Alert, Badge, Empty, Icon } from "../ui";

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
}) {
  const tasks = useResource<Task[]>("/tasks", version, true);
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
  return (
    <>
      <div className="panel toolbar market-toolbar">
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
              {task.name}
            </option>
          ))}
        </select>
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
        </select>
        <CategoryFilter
          marketId={filters.market_id}
          selected={filters.category || []}
          version={version}
          onChange={(category) => updateFilters({ category })}
        />
        <button aria-expanded={showRules} onClick={() => setShowRules((v) => !v)}>
          排除规则
        </button>
        <button
          onClick={() => {
            setDraftQuery("");
            updateFilters(defaultFilters());
          }}
        >
          重置
        </button>
      </div>
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
      <details className="market-definition list-help"><summary>查看新增、存量与资料变化的统计口径</summary><p>新增＝本轮首次进入该市场；存量＝此前已有且本轮再次找到；变化为其中资料发生变化的企业。未再次找到不代表停业，候选企业仍需业务核验。</p></details>
      <div className="section-title">
        <span className="muted">
          共 {companies.data?.total ?? "—"} 家 · 每页 50 家
        </span>
        <div className="actions">
          {["csv", "xlsx"].map((format) => (
            <button
              disabled={busy || companies.loading || !!companies.error}
              key={format}
              onClick={() =>
                act(
                  () =>
                    download(
                      `/exports?format=${format}&${filtersQuery(filters)}`,
                      `企业线索-${new Date().toISOString().slice(0, 10)}.${format}`,
                    ),
                  "导出文件已下载",
                )
              }
            >
              <Icon name="download" size={16} />
              {format.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      {!!Object.keys(selection).length && <div className="batch-toolbar" aria-label="批量客户匹配状态">
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
      </div>}
      <Resource {...companies} retry={refresh}>
        <section className="panel table-wrap">
          {companies.data?.items.length ? (
            <table>
              <thead>
                <tr>
                  <th>
                    <input
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
                    />
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
                      <input
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
                      />
                    </td>
                    <td>
                      <button
                        className="company-link"
                        onClick={() => openCompany(c.id)}
                      >
                        <span className="avatar">{c.name.slice(0, 1)}</span>
                        <span>
                          <strong>{c.name}</strong>
                          <small>
                            {[c.city, c.country].filter(Boolean).join(" / ") ||
                              "地区未记录"}
                          </small>
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
                    <td>
                      <div title="有明确采购岗位证据的联系方式；已按邮箱去重">
                        采购 {c.procurement_count ?? "—"}
                      </div>
                      <div title="去重后的社媒账号，含待确认账号，不重复计入已知采购入口">
                        其他社媒 {c.social_count ?? "—"}
                      </div>
                      <small title="去重后排除采购、招聘和人力资源用途的邮箱；用途不明的保留">
                        其他邮箱 {c.email_count ?? "—"}
                      </small>
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
            <Empty
              title={
                Object.entries(filters).some(
                  ([key, value]) => key !== "sort" && Boolean(value),
                )
                  ? "没有匹配的企业"
                  : "企业库还是空的"
              }
            >
              更新目标市场或人工录入企业后，资料会保存在这里。可调整筛选查看其他记录。
            </Empty>
          )}
        </section>
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
        导出当前筛选下的全部结果，不受分页限制，默认排除拒绝联系记录。CSV
        为企业表；XLSX 包含企业、联系人与地点表。
      </p>
    </>
  );
}
