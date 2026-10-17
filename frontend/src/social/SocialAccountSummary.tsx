import { date } from "../helpers";
import { cleanAccountTitle } from "../socialDisplay";
import { External } from "../ui";
import { discoveryName, platformNames } from "./presentation";
import { SourceStamp } from "./SourceStamp";
import type { Entry, SocialPage } from "./types";
export function SocialAccountSummary({
  entry,
  pages,
}: {
  entry: Entry;
  pages: SocialPage[];
}) {
  const platform = platformNames[entry.platform] || entry.platform;
  const page = pages.find((p) => p.entity_id === entry.id);
  const hasSource = entry.evidence.some((e) => e.method !== "legacy");
  const evidence = entry.evidence
    .filter((e) => !hasSource || e.method !== "legacy")
    .sort((a, b) => b.observed_at.localeCompare(a.observed_at));
  const named = evidence.find(
    (e) =>
      e.method === "platform_search" &&
      /；结果：(.+?)。名称匹配候选/.test(e.evidence),
  );
  const searchName = named?.evidence.match(/；结果：(.+?)。名称匹配候选/)?.[1];
  const title =
    cleanAccountTitle(page?.title) ||
    cleanAccountTitle(searchName) ||
    entry.label;
  const numeric = /^Page \d+$/.test(entry.label);
  const source = page?.title
    ? page.content_sources?.title?.source_url || page.source_url
    : named?.source_url;
  return (
    <div className="social-account-summary">
      <div className="row between">
        <span className="badge social-platform">数据来源：{platform}</span>
        <span className="badge">
          {entry.status === "confirmed"
            ? "人工已确认"
            : entry.status === "excluded"
              ? "不采用"
              : "归属待核对"}
        </span>
      </div>
      <h4 className="social-account-name">{title}</h4>
      <SourceStamp source={page?.content_sources?.title} />
      {source && (
        <small>
          <External url={source}>
            {page?.title ? "名称来自平台页面" : "名称来自站内搜索结果"}
          </External>
        </small>
      )}
      <dl className="social-account-fields">
        <div>
          <dt>{numeric ? "账号编号" : "用户名"}</dt>
          <dd>{numeric ? entry.label.replace("Page ", "") : entry.label}</dd>
        </div>
        <div>
          <dt>账号主页</dt>
          <dd>
            <External url={entry.url}>查看 {platform} 账号</External>
          </dd>
        </div>
        <div>
          <dt>账号简介</dt>
          <dd>
            {page?.description || "尚未采集账号简介"}
            <SourceStamp source={page?.content_sources?.description} />
          </dd>
        </div>
        {!!page?.fields?.length &&
          page.fields.map((f, i) => (
            <div key={i}>
              <dt>
                {(
                  {
                    email: "公开邮箱",
                    phone: "公开电话",
                    name: "公开姓名",
                    role: "岗位",
                    location: "所在地",
                    website: "网站链接",
                    website_text: "网站显示地址",
                    post_count: "帖子数",
                    followers: "粉丝数",
                    following: "关注数",
                    joined_at: "加入时间",
                  } as Record<string, string>
                )[f.key] || f.key}
              </dt>
              <dd>
                {f.value}
                <SourceStamp source={f} />{" "}
                <small>
                  <External url={f.source_url || page.source_url}>
                    {platform} 原文
                  </External>{" "}
                  · 归属待确认
                </small>
              </dd>
            </div>
          ))}
        <div>
          <dt>匹配依据</dt>
          <dd>
            {page?.match_signals?.length
              ? page.match_signals.map((m, i) => (
                  <div key={i}>
                    {m.text} <External url={m.source_url}>来源</External>
                  </div>
                ))
              : "尚无官网域名或电话一致的已采集证据"}
            <small>匹配线索不代表归属已确认</small>
          </dd>
        </div>
        <div>
          <dt>发现方式</dt>
          <dd>
            {Array.from(
              new Set(evidence.map((e) => discoveryName(e.method, platform))),
            ).join("、") || "未记录"}
          </dd>
        </div>
        {evidence[0] && (
          <div>
            <dt>{evidence[0].method === "legacy" ? "入库时间" : "最近发现"}</dt>
            <dd>{date(evidence[0].observed_at)}</dd>
          </div>
        )}
      </dl>
      {!page && (
        <p className="muted">
          目前仅发现账号线索。可在企业详情的“补充已有账号基础资料”中尝试补充简介和公开联系方式。
        </p>
      )}
    </div>
  );
}
