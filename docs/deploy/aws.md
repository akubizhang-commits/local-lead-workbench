# 部署到 AWS（首个客户体验版）

本文写给负责部署的管理员。一套环境服务一家客户（单租户）：一台 EC2 上用 Docker Compose 运行 `api`、`worker`、`caddy` 三个容器，数据（SQLite）放在 EC2 的 EBS 磁盘上的 Docker 数据卷里，每天自动备份，配置了 S3 桶时把每日备份上传到 S3。

本文所有云上操作都需要你在 AWS 控制台或 AWS CLI 里自己执行；仓库里的代码和脚本不会调用任何 AWS API，也不包含任何密钥。

## 1. 架构

```
浏览器 ──HTTPS 443──▶ caddy（证书、HTTP→HTTPS 跳转、访问日志脱敏）
                         │  compose 内部网络
                         ▼
                       api:8000（登录、接口、网页）◀──共享数据卷 /data──▶ worker（地图检索、官网复核、每日备份、S3 上传）
                                                         │
                                                         └── EBS gp3 根卷（Docker 卷 leadworkbench-data）
worker ──实例角色（IMDSv2）──▶ S3 桶（每日备份，可选）
```

- API 端口不对外发布，只有 Caddy 能访问。
- 账号由管理员用命令行创建；所有 `/api/*` 都要求登录；同一部署里的账号看到相同的数据。

## 2. 费用粗估（2026-10-21 查询）

| 项 | 美东 us-east-1 | 东京 ap-northeast-1 |
|---|---|---|
| EC2 t4g.medium（2 vCPU / 4 GiB，arm64，按需） | 约 0.0336 美元/小时 ≈ 24.5 美元/月 | 以 AWS 官网为准（通常高于美东） |
| EBS gp3 40 GiB | 约 3.2 美元/月（0.08 美元/GiB·月） | 以 AWS 官网为准 |
| 公网 IPv4（弹性 IP） | 约 3.6 美元/月（0.005 美元/小时） | 同左，以官网为准 |
| S3 备份（每天 1 份、保留 30 天、单份几十 MB） | 通常 < 1 美元/月 | 通常 < 1 美元/月 |
| 出流量 | 少量 | 少量 |
| 合计 | 约 32–35 美元/月 | 以 AWS Pricing Calculator 为准，预计略高 |

美东价格来自第三方价格汇总页（instances.vantage.sh、economize.cloud），不是 AWS 官方页面；东京的价格当天没查到可信来源。下单前请用 [AWS Pricing Calculator](https://calculator.aws/) 按你选的区域核对。x86 备选 t3.medium 价格相近。

## 3. 创建云资源（控制台或 CLI，由你执行）

1. **区域**：东京 `ap-northeast-1` 或美东 `us-east-1`。客户在亚洲选东京延迟更低；美东价格通常最低。
2. **EC2 实例**：Amazon Linux 2023（arm64），类型 `t4g.medium`（x86 备选 `t3.medium`，选 x86 AMI）。
   - 根卷：gp3，40 GiB，**加密**，**DeleteOnTermination=false**（实例被终止时磁盘保留）。
   - 元数据：**IMDSv2 必需**（HttpTokens=required），**跳数上限 2**（容器要多经一跳才能拿到实例角色凭据）。
3. **安全组**：
   - 22/TCP：只允许你自己的公网 IP（/32）。
   - 80/TCP、443/TCP：0.0.0.0/0（Let's Encrypt 需要从公网访问 80 端口）。
   - 其余全部关闭。
4. **弹性 IP**：分配并关联到实例。访问主机名是把 IP 的点换成短横线再加 `.sslip.io`，例如 IP `3.1.2.4` → `3-1-2-4.sslip.io`。
5. **S3 桶**（可选，建议开启）：
   - 与实例同区域；**阻止全部公共访问**；默认加密 SSE-S3。
   - **开启版本控制**：实例角色有写入权限，容器被攻破时可以用同名键覆盖旧备份；开启版本控制后旧版本仍在。
   - 生命周期规则（前缀 `leadworkbench/`）：当前版本 30 天后过期；非当前版本 30 天后永久删除；清理过期的删除标记。
6. **IAM 角色与实例配置文件**（附加到实例）。把 `<你的桶名>` 换成真实桶名：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "UploadDailyBackups",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::<你的桶名>/leadworkbench/*"
    },
    {
      "Sid": "RestoreFromS3OnlyWhenNeeded",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::<你的桶名>/leadworkbench/*", "arn:aws:s3:::<你的桶名>"],
      "Condition": {"StringLike": {"s3:prefix": ["leadworkbench/*"]}}
    }
  ]
}
```

第二条只在从 S3 拉回备份时需要，恢复完成后可以删掉。不要授予 `DeleteObject`、`PutBucket*`、`GetObjectVersion`。说明：`s3:prefix` 条件只作用于 `ListBucket`；如果控制台提示 `GetObject` 与该条件不匹配，把第二条拆成两条（`GetObject` 不带条件，`ListBucket` 带条件）。

## 4. 登录服务器并安装 Docker

```sh
ssh -i <你的密钥.pem> ec2-user@<弹性IP>
sudo dnf install -y git
git clone <仓库地址> leadworkbench && cd leadworkbench
git checkout <要部署的分支或标签>
sudo sh deploy/install.sh          # 安装 Docker、compose/buildx 插件（固定版本并校验 sha256）、可选 2 GiB swap
exit                                # 重新登录，让 docker 组生效
```

`deploy/install.sh` 可以重复执行，不包含任何密钥。加 `--swap` 参数会额外创建 2 GiB swap（t4g.medium 构建镜像时建议开启）。

## 5. 配置 `.env`

```sh
cd leadworkbench
cp .env.example .env
chmod 600 .env
vi .env
```

至少填写：

| 变量 | 示例 | 说明 |
|---|---|---|
| `LEAD_PUBLIC_HOST` | `3-1-2-4.sslip.io` | 访问主机名；有自己的域名后只改这一项 |
| `LEAD_BACKUP_S3_BUCKET` | `<你的桶名>` | 留空则只做本地备份 |
| `LEAD_BACKUP_S3_REGION` | `ap-northeast-1` | 设置了桶时必填 |
| `LEAD_TIMEZONE` | `Asia/Shanghai` | 每日备份按这个时区的 `LEAD_BACKUP_TIME`（默认 03:30） |

不要在 `.env` 里写任何 AWS 密钥；S3 上传使用实例角色。不要把 `.env` 提交到仓库。

## 6. 构建与启动

```sh
docker compose build          # 首次约 15–30 分钟（编译 Maps 程序、下载两套 Chromium）
docker compose up -d
docker compose ps             # 等 api、worker 显示 healthy
```

首次启动会做数据库迁移，期间网页显示 502 属正常。

## 7. 创建账号

```sh
docker compose exec api python -m leadworkbench.admin create-user <用户名>   # 交互输入两次密码，至少 12 位
docker compose exec api python -m leadworkbench.admin list-users
docker compose exec api python -m leadworkbench.admin reset-password <用户名>  # 重置后该账号在所有设备上退出
docker compose exec api python -m leadworkbench.admin disable-user <用户名>
docker compose exec api python -m leadworkbench.admin enable-user <用户名>
docker compose exec api python -m leadworkbench.admin logout-all [<用户名>]   # 强制退出
```

用户名 3–32 位字母、数字、点、下划线、短横线。不要把密码写在命令行参数或 shell 历史里，只在提示时输入。

## 8. 首次登录后确认存储保留策略

打开 `https://<LEAD_PUBLIC_HOST>`，登录 → 设置 → 存储保留 → 确认。未确认之前，任何备份和导出文件都不会被自动清理，每日备份会一直累积（设置页会提示）。确认后：每日备份保留最近 10 份；恢复前/修复前的自动备份另外保留最近 10 份。

## 9. HTTPS 证书

- 默认 `LEAD_TLS=acme`：Caddy 为 `<IP>.sslip.io` 自动申请 Let's Encrypt 证书并自动续期，80 端口自动跳转到 443。用 `docker compose logs caddy` 检查申请与续期。
- 申请失败（证书接口限流、80 端口不通）：在 `.env` 改为 `LEAD_TLS=internal`，执行 `docker compose up -d caddy`。此时使用 Caddy 内部自签证书，浏览器会提示“不安全”，需要手动选择继续访问。
- HSTS 只在 acme 模式下发送，有效期 1 天：从 acme 切到 internal 后，浏览器最多 1 天后允许“继续访问”。正式域名稳定后可以加长。
- 有自己的域名：DNS A 记录指向弹性 IP，`.env` 改 `LEAD_PUBLIC_HOST=<你的域名>`，`docker compose up -d`。允许的主机名和来源会自动跟着变。

## 10. 升级

```sh
# 先在网页“设置 → 备份与恢复”创建手动备份，或：
sh deploy/backup-now.sh
git pull
docker compose build
docker compose up -d
```

数据库迁移只能向前，不能降级。需要回退版本时，切回旧代码后用升级前的备份恢复。

## 11. 备份与恢复

- 每日备份：`LEAD_BACKUP_TIME`（默认 03:30，`LEAD_TIMEZONE`）由 worker 自动创建，文件名 `auto-daily-…sqlite3`；错过时间（例如当时在重启）会在 worker 启动 2 分钟后补做；失败 1 小时后重试。配置了桶时上传到 `s3://<桶>/leadworkbench/<年>/<月>/<文件名>`，同一份最多尝试 3 次。设置页显示最近一次备份与上传结果。
- 手动备份：网页“创建备份”，或 `sh deploy/backup-now.sh`（打印文件名）。手动备份不上传 S3。
- 恢复备份不会回滚账号：当前的账号和登录状态保持不变，备份里的旧账号、旧会话不会恢复（命令行 `--accounts backup` 除外，见下）。

### 网页恢复（常规）

设置 → 备份与恢复 → 选择备份 → 确认。有排队中或运行中的轮次时需要先暂停。

### 从 S3 拉回备份

```sh
aws s3 ls s3://<你的桶名>/leadworkbench/2026/10/
aws s3 cp s3://<你的桶名>/leadworkbench/2026/10/<文件名> /tmp/<文件名>
docker compose exec -T api sh -c 'umask 077 && cat > /data/backups/<文件名>' < /tmp/<文件名>
rm /tmp/<文件名>
```

文件名必须保持原名（`auto-daily-….sqlite3`）。然后在设置页选择该备份恢复。api 已停止时，把 `exec -T api` 换成 `run --rm --no-deps -T api`。

### 命令行恢复（网页打不开、页面一直提示服务不可用，或网页提示“无法读取当前账号”）

```sh
docker compose stop api worker
docker compose run --rm --no-deps api python -m leadworkbench.admin restore <文件名>
# 若提示无法打开当前数据库或无法读取当前账号：
docker compose run --rm --no-deps api python -m leadworkbench.admin restore <文件名> --accounts backup
docker compose up -d
```

- 命令会检查 api、worker 是否都已停止；没停会提示“请先停止 api / worker”并退出。
- 默认 `--accounts current`：保留当前账号。如果当前库里有排队中或运行中的轮次，命令会列出这些轮次并退出。处理办法：`docker compose up -d`，在网页“轮次”中暂停这些轮次，再 `docker compose stop api worker` 后重试；或者改用 `--accounts backup`。
- `--accounts backup`：使用备份里的账号，所有人需要重新登录；备份里没有账号时按提示执行 `create-user`。不检查轮次。
- 当前库会先另存到 `backups/auto-pre-restore-…sqlite3`；如果当前库已损坏，会原样复制到 `backups/damaged-<时间>/`。确认恢复成功后可以删除。

### 整机丢失

新建实例 → 第 3–7 步 → 从 S3 拉回备份 → 网页恢复（账号保持新建的那个），或命令行 `--accounts backup`（账号回到备份时的状态）。

## 12. 日志与排错

```sh
docker compose ps                         # 健康状态
docker compose logs -f api worker caddy   # 实时日志
docker compose exec api ls /data/logs     # 应用 JSON 日志
docker compose restart worker             # worker 显示 unhealthy 时
```

- 设置页可以导出诊断包（不含密码、会话、桶名和业务数据）。服务降级时诊断包不能下载，改用 `docker compose logs api`。
- Compose 不会因为 unhealthy 自动重启容器；容器退出时会按 `restart: unless-stopped` 自动重启。
- **警告：生产环境不要执行 `docker compose down -v`，`-v` 会删除数据卷（全部业务数据）。** 停止服务用 `docker compose stop` 或 `docker compose down`（不带 `-v`）。

## 13. 已知限制

- **Google Maps 在云服务器 IP 上可能被限流或要求验证**，需要上云后实测；出现验证码时立即暂停地图检索。
- 服务器部署版不支持登录社媒账号读取，也不支持需要登录的“站内搜索”（会显示“已跳过”）；账号发现和公开资料读取照常可用。Instaloader 不在镜像里。
- 没有 SearXNG，搜索候选阶段显示“未配置”。
- 单机单租户；同一部署里的账号看到相同数据，没有角色权限。
- 登录会话在容器重启后保留；登录失败计数和“可信 IP”在 api 重启后清零。
- **账号可能被他人暂时锁定**：知道用户名的任何人在 15 分钟内输错 5 次密码，该账号就会被锁 15 分钟。处理：管理员执行 `docker compose restart api` 清零计数（已登录的会话不受影响）；必要时给该用户另建一个不易猜到的用户名。
- 内部证书模式下浏览器会提示不安全。
- 本地模式（`scripts/start.sh`）的命令行恢复需要你自己先停止 `start.sh`。

## 14. 安全清单

- 不要提交 `.env`；`.env` 权限 600；里面不放 AWS 密钥。
- 22 端口只对你自己的 IP 开放；定期 `sudo dnf update`。
- 账号密码至少 12 位，只在交互提示中输入。
- S3 桶阻止公共访问、开启版本控制；实例角色只给 `PutObject`（恢复时临时加读权限）。
- IMDSv2 必需。
