import { date } from "../helpers";
import { socialStatus } from "../SocialDiscovery";
import { External } from "../ui";
import { SocialPosts } from "./SocialPosts";
import { SourceStamp } from "./SourceStamp";
import type { SocialPage } from "./types";
export function SocialPagePreview({
  page,
  showPosts = true,
}: {
  page: SocialPage;
  showPosts?: boolean;
}) {
  return (
    <div className="social-page-preview">
      <div className="row between">
        <strong>公开页面预览</strong>
        <span className={`badge social-state-${page.status}`}>
          {socialStatus(page.status)}
        </span>
      </div>
      <p>{page.message || "未提供访问说明"}</p>
      {page.title && (
        <p>
          <strong>{page.title}</strong>
          <SourceStamp source={page.content_sources?.title} />
        </p>
      )}
      {page.description && (
        <p className="pre-wrap">
          {page.description}
          <SourceStamp source={page.content_sources?.description} />
        </p>
      )}
      {page.provider && (
        <p className="muted">
          采集方式：
          {(
            {
              browser: "浏览器读取",
              http: "公开网页读取",
              instaloader: "Instaloader 读取",
              youtube_api: "YouTube API",
            } as Record<string, string>
          )[page.provider] || page.provider}
        </p>
      )}
      <p className="muted">
        {page.posts?.length
          ? "以下为读取到的帖子，不代表完整历史；"
          : "仅预览元信息，未采集完整帖子；"}
        页面可读不代表账号归属已确认。
      </p>
      {showPosts && <SocialPosts page={page} />}
      {!!page.fields?.length && (
        <details>
          <summary>页面提取字段（归属待确认）</summary>
          <p className="muted">
            邮箱、电话等仅为原文候选，尚未确认属于此企业或具体人员，未验证可联系性。
          </p>
          {page.fields.map((field, index) => (
            <div className="social-evidence" key={`${field.key}:${index}`}>
              <strong>
                {(
                  {
                    email: "邮箱",
                    phone: "电话",
                    name: "姓名",
                    role: "岗位",
                    location: "所在地",
                    website: "网站链接",
                    website_text: "网站显示地址",
                    post_count: "帖子数",
                    followers: "粉丝数",
                    following: "关注数",
                    joined_at: "加入时间",
                  } as Record<string, string>
                )[field.key] || field.key}{" "}
                · {field.value}
              </strong>
              <blockquote>{field.evidence || "未提供原文证据"}</blockquote>
              <SourceStamp
                source={{
                  ...field,
                  source_url: field.source_url || page.source_url,
                }}
              />
              <small>归属待确认</small>
            </div>
          ))}
        </details>
      )}
      {!!page.field_history?.length && (
        <details>
          <summary>历史字段值（{page.field_history.length}）</summary>
          <p className="muted">
            以下为此前采集的不同值，不等同于本次新值；当前字段以最近非空资料为准，归属仍需核对。
          </p>
          {page.field_history.map((field, index) => (
            <div className="social-evidence" key={`${field.key}:${index}`}>
              <strong>
                {field.key} · {field.value}
              </strong>
              <blockquote>{field.evidence || "未提供原文证据"}</blockquote>
              <SourceStamp source={field} />
            </div>
          ))}
        </details>
      )}
      {page.content_observed_at && (
        <p className="muted">
          保留的预览采集于 {date(page.content_observed_at)}
        </p>
      )}
      <p className="muted">
        访问检查于 {date(page.observed_at)} ·{" "}
        <External url={page.source_url}>查看预览来源</External>
      </p>
    </div>
  );
}
