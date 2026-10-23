# 服务器部署手册（给在 EC2 上执行部署的 AI 助手，例如 kiro-cli）

本文写给在服务器上执行部署的 AI 助手。人类管理员的完整说明见 `docs/deploy/aws.md`；本文只覆盖“代码已经 clone 到 EC2 之后”的步骤，并规定助手不能做的事。

目标：在一台 AWS 新加坡区域（`ap-southeast-1`）的 EC2 上，用 Docker Compose 启动 `api`、`worker`、`caddy`，通过 `https://<公网IP用短横线连接>.sslip.io` 提供服务，并通过 `deploy/verify.sh` 的全部检查。这是阶段 0：验证部署可用，并为管理员实测云端 Google Maps 做好准备。

## 0. 硬性规则（必须遵守）

- **不创建、不修改任何 AWS 资源**（实例、安全组、弹性 IP、IAM、S3 桶都由管理员在控制台完成）。不运行 `aws` 命令修改资源。
- **不读取实例角色凭据**：不要访问 `http://169.254.169.254/latest/meta-data/iam/` 下的任何路径，不要打印环境变量里的凭据。可以读 `public-ipv4`、`placement/region`。
- **不在 `.env` 或仓库里写任何密钥**。不要提交或推送任何文件到 git，不改 git 配置。
- **绝不执行 `docker compose down -v`**，也不要 `docker volume rm`：会删除全部业务数据。
- **不自己跑 Google Maps 查询**，不在服务器上访问 Google、社媒或客户官网做测试。Maps 实测由管理员在网页上操作（见第 8 节）。
- **不替用户设置或查看登录密码**。账号由管理员自己交互创建（第 6 节）；只有管理员明确要求时才用第 6 节的备选做法，并且不得打印密码。
- 每一步执行前说明要做什么；失败时先看日志、给出原因，不要反复重试同一条命令。

## 1. 前置检查

在仓库根目录执行：

```sh
uname -m                          # 必须是 aarch64（t4g.medium）
grep PRETTY_NAME /etc/os-release  # Amazon Linux 2023
df -h /                           # 至少 20 GiB 可用
free -h                           # 4 GiB 内存
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4; echo
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region; echo
```

- 拿不到 `public-ipv4` 时，请管理员确认已分配并关联弹性 IP。
- 区域应为 `ap-southeast-1`；不是时提醒管理员，但不阻止部署。
- 访问主机名 = 公网 IP 的点换成短横线 + `.sslip.io`，例如 `13.250.1.2` → `13-250-1-2.sslip.io`。

## 2. 安装 Docker

```sh
sudo sh deploy/install.sh --swap
```

脚本可以重复执行。它把当前用户加入 docker 组，但当前会话不会立即生效。之后的 docker 命令用以下任一方式执行：

- 请管理员断开 SSH 重新登录，或
- 在当前会话里用 `sg docker -c '<命令>'` 包一层，例如 `sg docker -c 'docker compose ps'`。

## 3. 配置 `.env`

```sh
cp -n .env.example .env
chmod 600 .env
```

用编辑命令（例如 `sed -i`）修改下列项，不要整体覆盖文件：

| 项 | 值 |
|---|---|
| `LEAD_PUBLIC_HOST` | 第 1 节算出的 `<IP>.sslip.io` |
| `LEAD_TLS` | `acme`（默认） |
| `LEAD_BACKUP_S3_BUCKET` | 管理员告诉你的桶名；没给就留空（只做本地备份） |
| `LEAD_BACKUP_S3_REGION` | 设置了桶时填 `ap-southeast-1` |

其余保持默认（`LEAD_TIMEZONE=Asia/Shanghai`、`LEAD_BACKUP_TIME=03:30`、会话 24 小时 / 7 天）。改完用 `grep -v '^#' .env | grep .` 给管理员看一遍。

## 4. 构建与启动

```sh
docker compose build        # 首次 15–30 分钟：编译 Maps 程序、下载 Chromium
docker compose up -d
docker compose ps
```

- 构建时间长，命令超时设置要足够（至少 45 分钟），或者放到后台并定期查看输出。
- 构建因内存不足失败（`signal: killed`、`exit code 137`）时，确认 `swapon --show` 里有 `/swapfile`，然后重试一次。
- 新加坡区域能直接访问 `proxy.golang.org`，不需要 `GOPROXY`。
- 首次启动会做数据库迁移，`api` 的健康检查最多等 10 分钟，期间网页返回 502 属正常。等 `api`、`worker` 都显示 `healthy`。

## 5. HTTPS 证书

```sh
docker compose logs --tail 100 caddy
```

- 看到 `certificate obtained successfully` 即成功。
- 5 分钟内没拿到、日志里有 acme / challenge 错误时：先确认安全组 80、443 对 `0.0.0.0/0` 开放、`LEAD_PUBLIC_HOST` 与弹性 IP 一致。仍失败就改 `.env` 为 `LEAD_TLS=internal`，执行 `docker compose up -d caddy`，并告诉管理员：浏览器会提示证书不安全，需要手动继续访问。

## 6. 创建登录账号

**推荐：请管理员自己在 SSH 终端里执行**（密码只在提示时输入，至少 12 位）：

```sh
docker compose exec api python -m leadworkbench.admin create-user <用户名>
```

用户名 3–32 位，字母、数字、点、下划线、短横线。

**备选（只有管理员明确要求助手代为创建时）**：生成随机密码写入只有本人可读的文件，从标准输入传给命令，**不要打印或读取该文件内容**：

```sh
umask 077
openssl rand -base64 18 > ~/leadworkbench-initial-password.txt
docker compose exec -T api python -m leadworkbench.admin create-user <用户名> < ~/leadworkbench-initial-password.txt
```

然后告诉管理员：自己用 `cat ~/leadworkbench-initial-password.txt` 查看密码，登录后执行 `docker compose exec api python -m leadworkbench.admin reset-password <用户名>` 改成自己的密码，再 `shred -u ~/leadworkbench-initial-password.txt` 删除文件。

## 7. 验证

```sh
sh deploy/verify.sh
```

必须全部 PASS。它检查：`.env` 权限和没有 AWS 密钥、`api`/`worker` healthy、`caddy` 运行、Maps 程序带稳定性补丁、`https://<host>/healthz` 返回 200、未登录访问 `/api/overview` 返回 401、公开介绍页 `/about` 返回 200 且 `/about/` 下其他路径不泄露文件、http 跳转 https、已有登录账号。脚本不打印任何密码或凭据。

有 FAIL 时按提示处理；处理不了就把输出和相关日志（`docker compose logs --tail 200 <服务>`）交给管理员。

## 8. 交给管理员的结果与下一步

向管理员报告：访问地址、`verify.sh` 的结果、证书方式（acme 或 internal）、是否配置了 S3。然后提醒管理员做阶段 0 实测（都在网页或本人电脑上操作，助手不要代做）：

1. 浏览器打开 `https://<host>` 登录，在“设置 → 存储保留”确认保留策略。
2. **云端 Maps 实测**：新建一个小市场（例如 San Jose、批发商、关键词 `metal roofing supply`），开始检索，**第一个查询单元完成后立即暂停**。出现验证码或“访问受限”提示就停止，不要重试。记录找到的企业数和提示内容。第二天同一时段再试 1 个查询单元。
3. **延迟实测**：在客户所在城市的网络下执行 3 次：
   `curl -o /dev/null -s -w "连接 %{time_connect}s 首字节 %{time_starttransfer}s\n" https://<host>/healthz`
4. **备份**：第二天确认 S3 里出现 `leadworkbench/<年>/<月>/auto-daily-…`，设置页显示“已上传到 S3”。

## 9. 升级（以后）

```sh
sh deploy/backup-now.sh
git pull
docker compose build
docker compose up -d
sh deploy/verify.sh
```

`/about` 是公开（免登录）的合作伙伴介绍页，Caddy 直接返回只读挂载的 `docs/partner/index.html`；`git pull` 后该文件有变化时执行 `docker compose up -d --force-recreate caddy` 才会生效，不要为此改应用的登录代码（见 `docs/deploy/aws-singapore.md` 第 12 步）。

数据库迁移只能向前。需要回退时切回旧代码，再用升级前的备份恢复（`docs/deploy/aws.md` 第 11 节）。

## 10. 常用排错

```sh
docker compose ps
docker compose logs --tail 200 api
docker compose logs --tail 200 worker
docker compose logs --tail 200 caddy
docker compose restart worker      # 只重启 worker，数据保留
```

停止服务用 `docker compose stop` 或 `docker compose down`（不带 `-v`）。
