import { date } from "../helpers";
import { External } from "../ui";
import type { SocialPage } from "./types";
export function SocialPosts({
  page,
  platform,
}: {
  page: SocialPage;
  platform?: string;
}) {
  return page.posts?.length ? (
    <section className="social-history" aria-label="读取到的帖子">
      <h4>已采集帖子（{page.posts.length}）</h4>
      <p className="muted">
        正文为采集时保存的内容；候选账号及帖子的企业归属尚需核对。
      </p>
      {page.posts.map((post, index) => (
        <article key={`${post.url}:${index}`}>
          <strong>
            {platform || "社交平台"} · {page.title || "账号名称未记录"}
          </strong>
          <p className="pre-wrap">{post.text || "未提供帖子正文"}</p>
          <p className="muted">
            {post.published_at ? (
              <>
                发布于{" "}
                <time dateTime={post.published_at}>
                  {date(post.published_at)}
                </time>
              </>
            ) : (
              "发布时间未提供"
            )}{" "}
            · 采集于{" "}
            {date(
              post.observed_at || page.content_observed_at || page.observed_at,
            )}
          </p>
          <External url={post.url}>原帖来源</External>
        </article>
      ))}
    </section>
  ) : null;
}
