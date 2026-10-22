#!/bin/sh
# Post-deploy checks on the server (AWS trial, docs/deploy/aws.md).
# Run from anywhere: sh deploy/verify.sh
# Prints one PASS/FAIL line per check and exits non-zero if any check fails.
# Never prints passwords, cookies, tokens or AWS credentials, and never calls AWS APIs.
set -u
cd "$(dirname -- "$0")/.."
fail=0
pass() { echo "PASS $1"; }
bad() { echo "FAIL $1"; fail=1; }

# ---- .env ----
if [ ! -f .env ]; then
  bad ".env 不存在：cp .env.example .env && chmod 600 .env"
  exit 1
fi
perm=$(stat -c %a .env 2>/dev/null || stat -f %Lp .env 2>/dev/null)
[ "${perm}" = 600 ] && pass ".env 权限 600" || bad ".env 权限是 ${perm}，应为 600：chmod 600 .env"
if grep -Eq '^[[:space:]]*(export[[:space:]]+)?AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)=' .env; then
  bad ".env 里有 AWS 密钥，请删除（S3 上传使用实例角色）"
else
  pass ".env 里没有 AWS 密钥"
fi
HOST=$(sed -n 's/^LEAD_PUBLIC_HOST=//p' .env | tail -n 1 | tr -d '[:space:]')
TLS=$(sed -n 's/^LEAD_TLS=//p' .env | tail -n 1 | tr -d '[:space:]')
TLS=${TLS:-acme}
# Only set for local verification (e.g. :18853); empty on the cloud server.
SUFFIX=$(sed -n 's/^LEAD_PUBLIC_PORT_SUFFIX=//p' .env | tail -n 1 | tr -d '[:space:]')
case "${HOST}" in
  ""|*"<"*|*">"*) bad "LEAD_PUBLIC_HOST 未设置" ;;
  *) pass "LEAD_PUBLIC_HOST=${HOST}（LEAD_TLS=${TLS}）" ;;
esac

# ---- containers ----
for svc in api worker; do
  cid=$(docker compose ps -q "${svc}" 2>/dev/null)
  if [ -z "${cid}" ]; then
    bad "${svc} 没有运行：docker compose up -d"
    continue
  fi
  state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${cid}" 2>/dev/null)
  [ "${state}" = healthy ] && pass "${svc} healthy" || bad "${svc} 状态：${state:-未知}（首次启动请等几分钟后重试）"
done
cid=$(docker compose ps -q caddy 2>/dev/null)
state=$( [ -n "${cid}" ] && docker inspect -f '{{.State.Status}}' "${cid}" 2>/dev/null )
[ "${state}" = running ] && pass "caddy running" || bad "caddy 状态：${state:-未运行}"

# ---- Maps program inside the image ----
if docker compose exec -T worker /app/tools/bin/google-maps-scraper -local-stability-info 2>/dev/null \
    | grep -q '"chromium_single_process":false'; then
  pass "Maps 程序已带稳定性补丁（chromium_single_process=false）"
else
  bad "Maps 程序自检未通过：docker compose exec worker /app/tools/bin/google-maps-scraper -local-stability-info"
fi

# ---- public HTTPS ----
case "${HOST}" in
  ""|*"<"*|*">"*) ;;
  *)
    k=""
    [ "${TLS}" = internal ] && k="-k"
    code=$(curl ${k} -s -o /dev/null -w '%{http_code}' --max-time 15 "https://${HOST}${SUFFIX}/healthz" 2>/dev/null)
    if [ "${code}" = 200 ]; then
      pass "https://${HOST}${SUFFIX}/healthz 返回 200"
    else
      bad "https://${HOST}${SUFFIX}/healthz 返回 ${code:-无响应}（检查安全组 80/443、弹性 IP 与主机名是否一致、docker compose logs caddy）"
    fi
    code=$(curl ${k} -s -o /dev/null -w '%{http_code}' --max-time 15 "https://${HOST}${SUFFIX}/api/overview" 2>/dev/null)
    [ "${code}" = 401 ] && pass "未登录访问 /api/overview 返回 401" || bad "未登录访问 /api/overview 返回 ${code:-无响应}，应为 401"
    if [ -z "${SUFFIX}" ]; then
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "http://${HOST}/" 2>/dev/null)
      case "${code}" in
        301|302|307|308) pass "http:// 跳转到 https://（${code}）" ;;
        *) bad "http://${HOST}/ 返回 ${code:-无响应}，应跳转到 https" ;;
      esac
    fi
    ;;
esac

# ---- accounts (names only) ----
# list-users prints a header line, then one tab-separated row per account with status 正常/已停用.
users=$(docker compose exec -T api python -m leadworkbench.admin list-users 2>/dev/null \
  | awk -F '\t' 'NR > 1 && $2 == "正常" { n++ } END { print n + 0 }')
if [ "${users:-0}" -gt 0 ] 2>/dev/null; then
  pass "已有可用的登录账号（${users} 个）"
else
  bad "还没有可用的登录账号：docker compose exec api python -m leadworkbench.admin create-user <用户名>"
fi

if [ "${fail}" = 0 ]; then
  echo "全部通过。浏览器打开 https://${HOST}${SUFFIX} 登录，然后在“设置 → 存储保留”确认保留策略。"
fi
exit "${fail}"
