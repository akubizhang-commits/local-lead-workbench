import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Alert, Field } from "./ui";

type Platform = "instagram" | "facebook" | "x";
type Connections = {
  reader: "http" | "browser";
  instagram_reader: "browser" | "instaloader";
  proxy_url: string;
  max_posts: number;
  youtube_key_configured: boolean;
  dependencies: { browser: boolean; instaloader: boolean };
  sessions: Partial<
    Record<Platform, { state: "closed" | "open"; message?: string }>
  >;
};
type Draft = Pick<Connections, "reader" | "instagram_reader" | "proxy_url"> & {
  max_posts: string;
};
const platformNames: Record<Platform, string> = {
  instagram: "Instagram",
  facebook: "Facebook",
  x: "Twitter（X）",
};
const toDraft = (data: Connections): Draft => ({
  reader: data.reader,
  instagram_reader: data.instagram_reader,
  proxy_url: data.proxy_url,
  max_posts: String(data.max_posts),
});

// Check the literal authority too: URL normalizes omitted/default ports and some IPv4 spellings.
export function validSocialProxy(value: string) {
  if (!value.trim()) return true;
  const match =
    /^(http|socks5):\/\/(localhost|127\.0\.0\.1|\[::1\]):([0-9]+)\/?$/i.exec(
      value.trim(),
    );
  return !!match && Number(match[3]) >= 1024 && Number(match[3]) <= 65535;
}

export function validSocialMaxPosts(value: string) {
  return /^\d+$/.test(value) && Number(value) >= 1 && Number(value) <= 5;
}

export function SocialConnections({
  onBlockedChange,
}: {
  onBlockedChange?: (blocked: boolean) => void;
}) {
  const [data, setData] = useState<Connections>();
  const [draft, setDraft] = useState<Draft>();
  const [key, setKey] = useState(""),
    [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState("load"),
    [failure, setFailure] = useState(""),
    [notice, setNotice] = useState("");
  const [stale, setStale] = useState(true);
  const lock = useRef(false);
  const mounted = useRef(false);
  const dirty =
    !!data &&
    !!draft &&
    (JSON.stringify(draft) !== JSON.stringify(toDraft(data)) ||
      key !== "" ||
      clearKey);
  const open =
    !!data &&
    Object.values(data.sessions).some((session) => session?.state === "open");
  const blocked = !!busy || stale || !data || dirty || open;
  useEffect(() => {
    onBlockedChange?.(blocked);
  }, [blocked, onBlockedChange]);
  useEffect(() => {
    mounted.current = true;
    void api<Connections>("/social/connections")
      .then((result) => {
        if (mounted.current) {
          setData(result);
          setDraft(toDraft(result));
          setStale(false);
        }
      })
      .catch((error) => {
        if (mounted.current)
          setFailure(
            error instanceof Error ? error.message : "读取连接配置失败",
          );
      })
      .finally(() => {
        if (mounted.current) setBusy("");
      });
    return () => {
      mounted.current = false;
    };
  }, []);
  function edit(patch: Partial<Draft>) {
    setDraft((current) => (current ? { ...current, ...patch } : current));
    setNotice("");
  }
  async function refresh() {
    if (lock.current || busy) return;
    lock.current = true;
    setBusy("refresh");
    setFailure("");
    try {
      const result = await api<Connections>("/social/connections");
      setData(result);
      setDraft((current) => current || toDraft(result));
      setStale(false);
    } catch (error) {
      setStale(true);
      setFailure(error instanceof Error ? error.message : "读取连接配置失败");
    } finally {
      lock.current = false;
      setBusy("");
    }
  }
  async function save() {
    if (lock.current || busy || !draft || stale || open) return;
    if (!validSocialProxy(draft.proxy_url)) {
      setFailure(
        "代理仅支持 http 或 socks5，地址限 localhost、127.0.0.1 或 [::1]，端口须为 1024–65535，不能包含账号、密码、路径或查询参数。",
      );
      return;
    }
    const maxPosts = Number(draft.max_posts);
    if (!validSocialMaxPosts(draft.max_posts)) {
      setFailure("帖子数量必须为 1–5 的整数。");
      return;
    }
    lock.current = true;
    setBusy("save");
    setFailure("");
    setNotice("");
    try {
      await api("/social/connections", "PUT", {
        reader: draft.reader,
        instagram_reader: draft.instagram_reader,
        proxy_url: draft.proxy_url.trim(),
        max_posts: maxPosts,
        ...(clearKey
          ? { youtube_api_key: "" }
          : key !== ""
            ? { youtube_api_key: key }
            : {}),
      });
      setKey("");
      setClearKey(false);
      // PUT's response shape is unspecified; GET is the source of truth.
      setStale(true);
      const result = await api<Connections>("/social/connections");
      setData(result);
      setDraft(toDraft(result));
      setStale(false);
      setNotice("连接配置已保存。");
    } catch (error) {
      setStale(true);
      setFailure(
        error instanceof Error
          ? error.message
          : "保存或重读配置失败，请刷新状态后重试",
      );
    } finally {
      lock.current = false;
      setBusy("");
    }
  }
  async function session(platform: Platform, action: "open" | "close") {
    if (
      lock.current ||
      busy ||
      (action === "open" && (dirty || stale || !data?.dependencies.browser))
    )
      return;
    lock.current = true;
    setBusy(`${platform}/${action}`);
    setFailure("");
    setNotice("");
    try {
      const result = await api<{ status: string; message?: string }>(
        `/social/browser/${platform}/${action}`,
        "POST",
      );
      setNotice(
        `${platformNames[platform]}：${result.message || "请求已处理，请查看会话状态"}${result.status ? `（${result.status}）` : ""}`,
      );
      setStale(true);
      const updated = await api<Connections>("/social/connections");
      setData(updated);
      setStale(false);
    } catch (error) {
      setStale(true);
      setFailure(
        error instanceof Error
          ? error.message
          : "浏览器操作失败，请刷新状态后重试",
      );
    } finally {
      lock.current = false;
      setBusy("");
    }
  }
  return (
    <section
      className="social-section"
      aria-label="社媒读取连接"
      aria-busy={!!busy}
    >
      <div className="section-title">
        <h3>社媒读取连接</h3>
        <button type="button" disabled={!!busy} onClick={() => void refresh()}>
          刷新连接状态
        </button>
      </div>
      <ol>
        <li>选择读取方式并保存配置。</li>
        <li>
          打开登录浏览器，在浏览器窗口中手动登录 Instagram / Facebook /
          Twitter（X）。
        </li>
        <li>登录完成后，点击下方“关闭并释放会话”，再运行发现任务。</li>
      </ol>
      <p className="muted">
        会话过期或读取时要求登录，请重新打开浏览器登录，再关闭并释放。打开、关闭状态不代表已登录或认证有效。这里不会发送私信或评论。
      </p>
      {busy && (
        <p role="status">
          {busy === "load" ? "正在读取连接配置…" : "正在处理连接请求…"}
        </p>
      )}
      {failure && <Alert>{failure}</Alert>}
      {notice && <p role="status">{notice}</p>}
      {stale && !busy && (
        <p className="muted">连接状态尚未确认，请刷新连接状态。</p>
      )}
      {draft && data && (
        <>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <fieldset disabled={!!busy || stale}>
              <legend>读取配置</legend>
              <div className="social-form-grid">
                <Field label="Facebook / Twitter（X）默认读取方式">
                  <select
                    value={draft.reader}
                    onChange={(event) =>
                      edit({ reader: event.target.value as Draft["reader"] })
                    }
                  >
                    <option value="http">HTTP</option>
                    <option value="browser">浏览器</option>
                  </select>
                </Field>
                <Field label="Instagram 读取方式">
                  <select
                    value={draft.instagram_reader}
                    onChange={(event) =>
                      edit({
                        instagram_reader: event.target
                          .value as Draft["instagram_reader"],
                      })
                    }
                  >
                    <option value="browser">浏览器</option>
                    <option value="instaloader">Instaloader</option>
                  </select>
                </Field>
                <Field label="本机代理 URL（可选）">
                  <input
                    value={draft.proxy_url}
                    maxLength={4096}
                    placeholder="http://127.0.0.1:7890"
                    onChange={(event) =>
                      edit({ proxy_url: event.target.value })
                    }
                  />
                </Field>
                <Field label="最多读取帖子数">
                  <input
                    type="number"
                    min="1"
                    max="5"
                    step="1"
                    required
                    value={draft.max_posts}
                    onChange={(event) =>
                      edit({ max_posts: event.target.value })
                    }
                  />
                </Field>
              </div>
              <p className="muted">
                默认读取方式控制浏览器模式。Instagram
                选择“浏览器”时，仅在默认读取方式为“浏览器”时使用浏览器，否则使用
                HTTP；选择 Instaloader 时独立生效，不受默认读取方式影响。
              </p>
              <p className="muted">
                代理可留空；协议仅限 http 或 socks5，地址仅限
                localhost、127.0.0.1 或 [::1]，必须明确填写 1024–65535
                的端口，不允许账号或密码。
              </p>
              <Field label="YouTube API key（输入新密钥以替换）">
                <input
                  type="password"
                  autoComplete="new-password"
                  value={key}
                  disabled={clearKey}
                  onChange={(event) => {
                    setKey(event.target.value);
                    setNotice("");
                  }}
                />
              </Field>
              <p className="muted">
                密钥{data.youtube_key_configured ? "已配置" : "未配置"}
                ；不会回填已保存密钥。留空保留现有密钥。
              </p>
              <label className="check-line">
                <input
                  type="checkbox"
                  checked={clearKey}
                  onChange={(event) => {
                    setClearKey(event.target.checked);
                    setKey("");
                    setNotice("");
                  }}
                />
                清除已保存的 YouTube API key
              </label>
              <div className="actions">
                <button className="primary" type="submit" disabled={open}>
                  保存连接配置
                </button>
              </div>
            </fieldset>
          </form>
          {dirty && (
            <p className="muted">
              有未保存修改，请先保存连接配置，再打开登录浏览器或运行发现。
            </p>
          )}
          <p className="muted">
            浏览器依赖：{data.dependencies.browser ? "可用" : "不可用"} ·
            Instaloader 依赖：
            {data.dependencies.instaloader ? "可用" : "不可用"}
            。依赖可用不代表平台访问成功。
          </p>
          {((draft.reader === "browser" && !data.dependencies.browser) ||
            (draft.instagram_reader === "instaloader" &&
              !data.dependencies.instaloader)) && (
            <Alert>
              所选读取方式的依赖不可用，请安装对应依赖或选择其他读取方式。
            </Alert>
          )}
          {(["instagram", "facebook", "x"] as const).map((platform) => (
            <article className="social-card" key={platform}>
              <div className="row between">
                <strong>{platformNames[platform]}</strong>
                <span className="badge">
                  {stale
                    ? "会话状态待刷新"
                    : !data.sessions[platform]
                      ? "未提供会话状态"
                      : data.sessions[platform]?.state === "open"
                        ? "浏览器已打开"
                        : "浏览器已关闭"}
                </span>
              </div>
              {data.sessions[platform]?.message && (
                <p>{data.sessions[platform]?.message}</p>
              )}
              <div className="actions">
                <button
                  type="button"
                  disabled={
                    !!busy ||
                    stale ||
                    dirty ||
                    !data.dependencies.browser ||
                    !data.sessions[platform] ||
                    data.sessions[platform]?.state === "open"
                  }
                  onClick={() => void session(platform, "open")}
                >
                  打开 {platformNames[platform]} 登录浏览器
                </button>
                <button
                  type="button"
                  disabled={
                    !!busy ||
                    !data.sessions[platform] ||
                    (!stale && data.sessions[platform]?.state === "closed")
                  }
                  onClick={() => void session(platform, "close")}
                >
                  关闭并释放 {platformNames[platform]} 会话
                </button>
              </div>
            </article>
          ))}
          {open && (
            <p role="status">
              登录浏览器仍打开，请点击“关闭并释放会话”后再保存配置或运行发现任务。
            </p>
          )}
        </>
      )}
    </section>
  );
}
