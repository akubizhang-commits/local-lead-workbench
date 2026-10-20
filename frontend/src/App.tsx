import { useEffect, useRef, useState } from "react";
import { actionMessage } from "./errors";
import { runTitle } from "./MarketRecheck";
import { api, resetSession } from "./api";
import { CompanyForm, TaskForm } from "./forms";
import { date, filtersQuery, type Filters } from "./helpers";
import { useResource } from "./hooks";
import { MarketHeadingActions, TaskActivityPage, marketSubline } from "./pages/TaskActivityPage";
import { backFromRefreshDetail, defaultFilters, updateListFilters, type View } from "./navigation";
import { ChangesPage } from "./pages/ChangesPage";
import { CompaniesPage } from "./pages/CompaniesPage";
import { RetentionMarker } from "./RetentionPanel";
import { CompanyDetailPage } from "./pages/CompanyDetailPage";
import { MarketsPage } from "./pages/MarketsPage";
import { ReviewsPage } from "./pages/ReviewsPage";
import { SettingsPage } from "./pages/SettingsPage";
import type {
  Backup,
  Company,
  CompanyDetail,
  Overview,
  Run,
  Task,
} from "./types";
import { Alert, Icon, Modal } from "./ui";
import { useNavigation } from "./useNavigation";
import { workerStatus } from "./workerStatus";

const navigation: [View, string, string][] = [
  ["tasks", "目标市场", "grid"],
  ["companies", "企业库", "company"],
  ["reviews", "待审核", "check"],
  ["changes", "更新记录", "clock"],
  ["settings", "设置", "settings"],
];
const descriptions: Record<View, string> = {
  tasks: "按地区建立目标市场，持续发现新增企业、复核存量资料。",
  companies: "整理企业资料、查看来源，为下一次联系做好准备。",
  detail: "",
  reviews: "对照来源做出判断：自动采集给出建议，由你决定是否采用。",
  changes: "追踪资料变化，保留每一次判断的来龙去脉。",
  settings: "管理本地数据、来源能力与定时维护。",
};
export default function App() {
  const { route, navigate } = useNavigation();
  const { view, selected, filters, page } = route;
  function setView(view: View) {
    navigate({
      ...route,
      view,
      selected: view === "detail" ? selected : null,
      account: undefined,
      task: undefined,run:undefined,item:undefined,activityStatus:undefined,listRun:undefined,
    });
  }
  function setPage(update: (page: number) => number) {
    navigate({ ...route, page: update(page) });
  }
  const [version, setVersion] = useState(0);
  function setTaskDetail(task: Task | null) {
    navigate({...route,view:"tasks",task:task?.id,run:undefined,item:undefined,activityStatus:undefined,page:1});
  }
  const [modal, setModal] = useState<"task" | "company" | "edit" | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  useEffect(() => {
    setModal(null);
    setActionError("");
  }, [view, selected, filtersQuery(filters), page]);
  const [restore, setRestore] = useState<Backup | null>(null);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const overview = useResource<Overview>("/overview", version, true);
  const tasks = useResource<Task[]>(
    view === "tasks" ? "/tasks?archived=include" : null,
    version,
    true,
  );
  const detail = useResource<CompanyDetail>(
    view === "detail" && selected
      ? `/companies/${encodeURIComponent(selected)}`
      : null,
    version,
  );
  const marketTask = view === "tasks" ? tasks.data?.find((t) => t.id === route.task) : undefined;
  const marketTitle = route.task === "standalone" ? "单家官网复核" : marketTask?.name || "目标市场";
  function updateFilters(patch: Partial<Filters>) {
    navigate(updateListFilters(route, patch));
  }
  function openMarket(id: string, extra: Partial<Filters> = {}) {
    navigate({
      view: "companies",
      selected: null,
      filters: { ...defaultFilters(), market_id: id, ...extra },
      page: 1,
    });
  }
  const accountScroll = useRef(0);
  function openAccount(id: string) {
    accountScroll.current = window.scrollY;
    navigate({ ...route, account: id });
  }
  function backToCompany() {
    navigate({ ...route, account: undefined });
  }
  useEffect(() => {
    const timer = window.setTimeout(
      () => window.scrollTo(0, route.account ? 0 : accountScroll.current),
      100,
    );
    return () => window.clearTimeout(timer);
  }, [route.account]);
  function refresh() {
    resetSession();
    setVersion((v) => v + 1);
  }
  function openCompany(id: string) {
    navigate({ ...route, selected: id, view: "detail", account: undefined });
  }
  async function act(
    work: () => Promise<unknown>,
    message: string | ((result: unknown) => string),
  ) {
    if (busy) return;
    setBusy(true);
    setActionError("");
    setNotice("");
    try {
      const result = await work();
      setNotice(typeof message === "function" ? message(result) : message);
      setVersion((v) => v + 1);
    } catch (e) {
      const conflict = (e as { status?: number }).status === 409;
      setActionError(actionMessage(e));
      if (conflict) {
        setVersion((v) => v + 1);
      }
    } finally {
      setBusy(false);
    }
  }
  function quickStatus(company: Company, status: string) {
    if (status === company.status) return;
    void act(
      () =>
        api(`/companies/${encodeURIComponent(company.id)}`, "PATCH", {
          revision: company.revision,
          status,
        }),
      "客户匹配状态已保存",
    );
  }
  return (
    <div className="app">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setView("tasks");
          }}
        >
          <span className="brand-symbol">
            <Icon name="search" size={25} />
          </span>
          <div>
            寻迹<span>本地线索工作台</span>
          </div>
        </a>
        <nav aria-label="主导航">
          {navigation.map(([id, title, icon]) => (
            <button
              key={id}
              aria-label={title}
              title={title}
              className={
                (view === "detail" ? "companies" : view) === id ? "active" : ""
              }
              onClick={() => {
                setView(id);
                setActionError("");
              }}
              aria-current={
                (view === "detail" ? "companies" : view) === id
                  ? "page"
                  : undefined
              }
            >
              <Icon name={icon} />
              <span>{title}</span>
              {id === "reviews" && !!overview.data?.pending_reviews && (
                <em>{overview.data.pending_reviews}</em>
              )}
              {id === "settings" && <RetentionMarker overview={overview.data} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <Icon name="shield" />
            <div>
              <strong>数据保存在本机</strong>
              <small>公开来源 · 人工审核</small>
            </div>
          </div>
          <div className="profile">
            <span aria-hidden="true">本</span>
            <div>
              本地工作空间<small>内部测试版</small>
            </div>
            <span
              className={`dot ${overview.data && !overview.error ? "on" : ""}`}
            />
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <nav className="breadcrumbs" aria-label="页面位置">
            <span>工作空间</span>
            <span className="slash" aria-hidden="true">/</span>
            {view === "tasks" && route.task ? (
              <><button className="text-button" onClick={()=>setView("tasks")}>目标市场</button><span className="slash" aria-hidden="true">/</span>{route.item?<button className="text-button" title={marketTitle} onClick={()=>navigate(backFromRefreshDetail(route))}>{marketTitle}</button>:<span aria-current="page" title={marketTitle}>{marketTitle}</span>}{route.item&&<><span className="slash" aria-hidden="true">/</span><span aria-current="page">官网复核详情</span></>}</>
            ) : view === "detail" ? (
              <>
                <button
                  className="text-button"
                  onClick={() => setView("companies")}
                >
                  企业库
                </button>
                <span className="slash" aria-hidden="true">/</span>
                <>
                  {route.account ? (
                    <>
                      <button className="text-button" onClick={backToCompany}>
                        企业详情
                      </button>
                      <span className="slash" aria-hidden="true">/</span>
                      <span aria-current="page">
                        {route.account?.startsWith("contact:")
                          ? "联系人详情"
                          : "账号详情"}
                      </span>
                    </>
                  ) : (
                    <span aria-current="page">企业详情</span>
                  )}
                </>
              </>
            ) : (
              <span aria-current="page">
                {navigation.find((n) => n[0] === view)?.[1]}
              </span>
            )}
          </nav>
          <div className="row">
            <span className={`connection ${overview.error ? "offline" : ""}`}>
              <span className="dot" />
              {overview.error
                ? "本地服务未连接"
                : overview.loading
                  ? "正在连接本地服务"
                  : "本地服务已连接"}
            </span>
            <span
              className={`connection ${overview.data?.worker?.alive ? "" : "offline"}`}
            >
              {overview.error
                ? "后台状态未知"
                : workerStatus(overview.data?.worker).text}
            </span>
            <button
              className="icon-button"
              title="刷新数据"
              aria-label="刷新数据"
              onClick={refresh}
            >
              <Icon name="refresh" size={17} />
            </button>
          </div>
        </header>
        <main>
          <div className={`page-heading view-${view}`}>
            <div>
              {view === "tasks" && route.task && <button className="text-button page-back" onClick={()=>route.item?navigate(backFromRefreshDetail(route)):setView("tasks")}>{route.item?(route.task==="standalone"?"← 返回单家官网复核":"← 返回市场详情"):"← 返回目标市场"}</button>}
              {view === "detail" && <button className="text-button page-back" onClick={route.account?backToCompany:()=>setView("companies")}>{route.account?"← 返回企业详情":"← 返回企业库"}</button>}
              <h1>
                {view === "tasks" && route.task ? (route.item ? "官网复核详情" : marketTitle) : view === "detail"
                  ? route.account
                    ? route.account.startsWith("contact:")
                      ? "联系人详情"
                      : "账号详情"
                    : "企业详情"
                  : navigation.find((n) => n[0] === view)?.[1]}
              </h1>
              {view==='tasks'&&route.task?(route.item?<p>查看这次官网复核读取了哪些网页、结果和历次尝试。</p>:marketTask&&<p>{marketSubline(marketTask)}</p>):descriptions[view]&&<p>{descriptions[view]}</p>}
            </div>
            <div className="actions">
              {view === "tasks" && marketTask && !route.item && (
                <MarketHeadingActions task={marketTask} version={version} busy={busy} act={act}
                  openMarket={openMarket} onOpenMarket={(id) => navigate({ ...route, task: id, run: undefined, item: undefined, page: 1 })} />
              )}
              {view === "tasks" && !route.task && !route.item && (
                <button className="primary" onClick={() => setModal("task")}>
                  <Icon name="plus" size={17} />
                  创建目标市场
                </button>
              )}
          {view === "companies" && (
                <button className="primary" onClick={() => setModal("company")}>
                  <Icon name="plus" size={17} />
                  人工录入企业
                </button>
              )}
              {view === "detail" && !route.account && detail.data && (
                <>
                  <button onClick={() => setModal("edit")}>编辑资料</button>
                  <button
                    className="primary"
                    disabled={busy || !detail.data.company.website}
                    onClick={() =>
                      act(
                        () =>
                          api(
                            `/companies/${encodeURIComponent(selected!)}/refresh`,
                            "POST",
                          ),
                        (result) => {
                          const run = result as Run;
                          return run?.already_queued
                            ? `已在「${runTitle(run, detail.data?.company.name)}」的复核队列中，无需重复发起`
                            : "官网复核已加入队列，可在“目标市场 › 单家官网复核”查看进度";
                        },
                      )
                    }
                  >
                    <Icon name="refresh" size={17} />
                    复核官网
                  </button>
                </>
              )}
            </div>
          </div>
          {notice && (
            <div className="toast" role="status">
              <Icon name="check" size={18} />
              {notice}
              <button aria-label="关闭提示" onClick={() => setNotice("")}>
                ×
              </button>
            </div>
          )}
          {actionError && <Alert>{actionError}</Alert>}
          {view === "tasks" && (route.task || route.item) && <TaskActivityPage route={route} navigate={navigate} tasks={tasks.data||[]} version={version} worker={overview.data?.worker} busy={busy} act={act} refresh={refresh} openCompany={openCompany} openMarket={openMarket}/>}
          {view === "tasks" && !route.task && !route.item && (
            <MarketsPage
              overview={overview}
              tasks={tasks}
              busy={busy}
              act={act}
              refresh={refresh}
              setTaskDetail={setTaskDetail}
              openStandalone={()=>navigate({...route,task:"standalone",run:undefined,item:undefined,page:1,activityStatus:undefined})}
              openMarket={openMarket}
              setModal={setModal}
            />
          )}
          {view === "companies" && (
            <CompaniesPage
              version={version}
              filters={filters}
              page={page}
              busy={busy}
              act={act}
              refresh={refresh}
              updateFilters={updateFilters}
              setPage={setPage}
              openCompany={openCompany}
              quickStatus={quickStatus}
              exportDays={overview.data?.retention?.export_days}
            />
          )}
          {view === "detail" && (
            <CompanyDetailPage
              selected={selected}
              detail={detail}
              account={route.account}
              version={version}
              busy={busy}
              refresh={refresh}
              backToCompany={backToCompany}
              quickStatus={quickStatus}
              openCompany={openCompany}
              openAccount={openAccount}
              setView={setView}
            />
          )}
          {view === "reviews" && (
            <ReviewsPage
              version={version}
              busy={busy}
              act={act}
              refresh={refresh}
              openCompany={openCompany}
            />
          )}
          {view === "changes" && (
            <ChangesPage
              version={version}
              refresh={refresh}
              openCompany={openCompany}
            />
          )}
          {view === "settings" && (
            <SettingsPage
              version={version}
              overview={overview}
              busy={busy}
              act={act}
              refresh={refresh}
              setRestore={setRestore}
              setConfirmRestore={setConfirmRestore}
            />
          )}
          <div className="page-footer">
            <span>寻迹 · 本地线索工作台</span>
            <span>真实查询 / 可追溯证据 / 人工判断</span>
          </div>
        </main>
      </div>
      {modal === "task" && (
        <Modal title="创建目标市场" onClose={() => setModal(null)}>
          <TaskForm
            onCancel={() => setModal(null)}
            onDone={() => {
              setModal(null);
              setVersion((v) => v + 1);
              setNotice("目标市场已创建，点击开始检索后执行真实查询");
            }}
          />
        </Modal>
      )}
      {(modal === "company" || modal === "edit") && (
        <Modal
          title={modal === "edit" ? "编辑企业资料" : "人工录入企业"}
          onClose={() => setModal(null)}
        >
          <CompanyForm
            company={modal === "edit" ? detail.data?.company : undefined}
            onCancel={() => setModal(null)}
            onDone={(c) => {
              setModal(null);
              openCompany(c.id);
              setVersion((v) => v + 1);
              setNotice("企业资料已保存");
            }}
          />
        </Modal>
      )}
      {restore && (
        <Modal
          title="确认恢复备份"
          onClose={() => {
            if (!busy) setRestore(null);
          }}
        >
          <div className="form-body">
            <Alert>
              将用此备份替换当前业务数据，恢复前会自动备份当前数据。有排队中或运行中的轮次时不能恢复：暂停或等待这些轮次结束即可，不需要取消。
            </Alert>
            <p>
              <strong>{restore.name}</strong>
            </p>
            <p className="muted">备份时间：{date(restore.created_at)}</p>
            <label className="check-line">
              <input
                type="checkbox"
                checked={confirmRestore}
                onChange={(e) => setConfirmRestore(e.target.checked)}
              />
              我确认恢复该备份，理解当前数据会被替换
            </label>
            {actionError && <Alert>{actionError}</Alert>}
          </div>
          <footer>
            <button disabled={busy} onClick={() => setRestore(null)}>
              取消
            </button>
            <button
              className="danger"
              disabled={!confirmRestore || busy}
              onClick={() =>
                act(async () => {
                  await api("/backups/restore", "POST", { name: restore.name });
                  resetSession();
                  setRestore(null);
                }, "备份已恢复，正在重新读取资料")
              }
            >
              {busy ? "恢复中…" : "确认恢复"}
            </button>
          </footer>
        </Modal>
      )}
    </div>
  );
}
