import { api } from "../api";
import { startConfirmText } from "../QueryPreview";
import { RESTORED } from "./TaskActivityPage";
import { date, runMessage } from "../helpers";
import type { Overview, Task } from "../types";
import { Badge, Empty, Icon } from "../ui";
import { WorkerHealth } from "../WorkerHealth";

import { Resource, type ResourceState } from "../Resource";
import type { Action } from "./pageTypes";

export function MarketsPage({
  overview,
  tasks,
  busy,
  act,
  refresh,
  setTaskDetail,
  openMarket,
  openStandalone,
  setModal,
}: {
  overview: ResourceState<Overview>;
  tasks: ResourceState<Task[]>;
  busy: boolean;
  act: Action;
  refresh: () => void;
  setTaskDetail: (task: Task) => void;
  openMarket: (id: string) => void;
  openStandalone: () => void;
  setModal: (modal: "task") => void;
}) {
  const active = (tasks.data || []).filter((task) => !task.archived_at);
  const archived = (tasks.data || []).filter((task) => !!task.archived_at);
  return (
    <>
      <WorkerHealth
        worker={overview.error ? undefined : overview.data?.worker}
        onRefresh={refresh}
      />
      {overview.data?.maps_policy && <section className="panel" aria-label="地图访问节奏">
        <strong>地图采集 · 低速模式</strong>
        <p>{runMessage(overview.data.maps_policy.message)}</p>
        {overview.data.maps_policy.manual_required && <>
          <p>冷却结束后，请确认访问已恢复再继续。活动任务的保留队列会自动继续；已暂停或结束的轮次需在任务列表点击继续。</p>
          <button disabled={busy || overview.data.maps_policy.retry_after_seconds > 0}
            onClick={()=>act(()=>api('/maps/resume','POST',{}),'已解除地图保护暂停，活动任务的保留队列将逐个继续')}>
            恢复地图采集
          </button>
        </>}
      </section>}
      <div className="metrics">
        {[
          ["企业线索", overview.data?.companies, "company", "已保存的企业记录"],
          [
            "联系记录",
            overview.data?.contacts,
            "detail",
            "含历史及不采用的记录",
          ],
          ["排队等待", overview.data?.queued_runs, "clock", "等待后台处理"],
          [
            "执行中任务",
            overview.data?.running_runs,
            "play",
            "以后台心跳确认是否在线",
          ],
          [
            "等待人工审核",
            overview.data?.pending_reviews,
            "check",
            "确认资料与来源归属",
          ],
        ].map(([title, value, icon, note]) => (
          <div className="metric" key={String(title)}>
            <div>
              <span>{title}</span>
              <Icon name={String(icon)} />
            </div>
            <strong>{overview.error ? "—" : (value ?? "—")}</strong>
            <small>{note}</small>
          </div>
        ))}
      </div>
      <section className="source-strip">
        <Icon name="globe" />
        <div>
          <strong>真实来源，持续维护</strong>
          <p>发现企业 → 官网补全 → 人工核验 → 定期复核</p>
        </div>
        <div className="source-status">
          {(["maps", "website"] as const).map((key) => (
            <span key={key} title={overview.data?.source_status[key]?.message}>
              <i
                className={`dot ${overview.data?.source_status[key]?.available ? "on" : ""}`}
              />
              {key === "maps" ? "地图发现" : "官网读取"} ·{" "}
              {overview.error
                ? "状态未知"
                : overview.data?.source_status[key]?.available
                  ? key === "maps"
                    ? "已安装"
                    : "可用"
                  : overview.loading
                    ? "读取中"
                    : "不可用"}
            </span>
          ))}
        </div>
      </section>
      <div className="section-title">
        <h2>
          市场检索任务列表 <span className="count">{tasks.data ? active.length : "—"}</span>
        </h2>
        <span className="muted">持续积累 · 本轮变化可追溯</span>
      </div>
      <Resource {...tasks} retry={refresh}>
        {active.length ? (
          <div className="table-wrap"><table><thead><tr><th>市场检索任务</th><th>目标地区</th><th>最近状态</th><th>最近检索</th><th>操作</th></tr></thead><tbody>
            {active.map(task=><tr key={task.id}><td><button className="text-button" onClick={()=>setTaskDetail(task)}>{task.name}</button><p className="muted">{task.keywords.join(' · ')}</p></td>
            <td>{[task.country,task.state,task.county,task.city].filter(Boolean).join(' / ')}{(task.geography?.county_ids.length||0)>1&&<small className="muted">跨 {task.geography?.county_ids.length} 个县或同级地区 · 完整城市范围</small>}</td><td>{task.latest_run?<Badge value={task.latest_run.status}/>:"尚未运行"}</td><td>{date(task.latest_run?.created_at)}</td>
            <td><div className="actions"><button onClick={()=>setTaskDetail(task)}>资料复核列表</button><button onClick={()=>openMarket(task.id)}>企业库</button>
            <button disabled={busy||['running','queued','paused'].includes(task.latest_run?.status||'')} onClick={()=>{if(window.confirm(startConfirmText(task.query_estimate)))act(()=>api(`/tasks/${encodeURIComponent(task.id)}/runs`,"POST"),"市场更新已加入运行队列");}}>{task.latest_run?'更新市场':'开始检索'}</button></div></td></tr>)}
          </tbody></table></div>
        ) : (
          <section className="panel">
            <Empty
              title="创建你的第一个目标市场"
              action={
                <button className="primary" onClick={() => setModal("task")}>
                  <Icon name="plus" size={16} />
                  创建目标市场
                </button>
              }
            >
              选择地区与企业类型，建立可持续更新的市场企业库。
            </Empty>
          </section>
        )}
      </Resource>
      {!!archived.length && <details className="panel archived-markets"><summary>已归档市场（{archived.length}）</summary>
        <p className="muted">已归档的市场不会开始新一轮，也不会按计划自动更新；企业和历史记录都已保留。</p>
        <div className="table-wrap"><table><thead><tr><th>市场</th><th>归档时间</th><th>操作</th></tr></thead><tbody>
          {archived.map(task=><tr key={task.id}><td>{task.name}</td><td>{date(task.archived_at)}</td>
            <td><button disabled={busy} onClick={()=>act(()=>api(`/tasks/${encodeURIComponent(task.id)}/restore`,"POST"),RESTORED)}>恢复</button></td></tr>)}
        </tbody></table></div></details>}
      <section className="panel"><div className="row between"><div><h2>企业单独复核</h2><p className="muted">查看从企业详情或定时计划发起的复核记录。</p></div><button onClick={openStandalone}>查看复核列表</button></div></section>
    </>
  );
}
