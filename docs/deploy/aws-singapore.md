# 新加坡区域部署手册（ap-southeast-1，逐步操作）

本文按顺序带你在 AWS 新加坡区域（`ap-southeast-1`）部署一套寻迹线索工作台，并完成阶段 0 实测。每一步先给控制台操作，再给可选的 AWS CLI 等价命令。系统结构见 [系统架构设计](../architecture.md)；更完整的参考说明见 [部署到 AWS](aws.md)。

约定：

- 尖括号 `<…>` 是需要你替换的值，替换时连尖括号一起删掉。本文用到的示例值：桶名 `leadworkbench-backup-<你的AWS账号ID>`、角色名 `leadworkbench-ec2`、安全组名 `leadworkbench-sg`、密钥对名 `leadworkbench-key`。
- AWS CLI 命令在**你自己的电脑**上执行（需要你本人的管理员凭据），不是在 EC2 上。全部用控制台操作的话，可以跳过所有 CLI 代码块。
- 仓库里的代码和脚本不会调用任何 AWS API，也不包含任何密钥。所有云资源都由你创建。

## 0. 准备与费用

需要准备：

- 一个 AWS 账号，控制台登录权限（能创建 S3、IAM、EC2）。
- 你当前电脑的公网 IP（用于只对你开放 SSH，可在浏览器搜索“我的 IP”查看）。
- 一台能用 `ssh` 的电脑（macOS / Linux 终端，或 Windows 的 PowerShell）。
- 如果用 kiro-cli 部署：在 EC2 上安装并登录 kiro-cli（按 kiro-cli 官方文档操作）。

费用组成（按需计费，**本文不列新加坡的具体单价**，请下单前用 [AWS Pricing Calculator](https://calculator.aws/) 选 “Asia Pacific (Singapore)” 核对）：

| 项 | 规格 | 官方价格页 |
|---|---|---|
| EC2 实例 | `t4g.medium`（2 vCPU / 4 GiB，arm64），按小时计费，停机后不收 | [EC2 按需价格](https://aws.amazon.com/ec2/pricing/on-demand/) |
| EBS 磁盘 | gp3 40 GiB，按月计费，**停机后照收** | [EBS 价格](https://aws.amazon.com/ebs/pricing/) |
| 公网 IPv4（弹性 IP） | 1 个，按小时计费，**停机后照收** | [VPC 价格（Public IPv4）](https://aws.amazon.com/vpc/pricing/) |
| S3 | 每天 1 份备份（通常几十 MB），保留 30 天 | [S3 价格](https://aws.amazon.com/s3/pricing/) |
| 数据传出 | 少量（网页访问、下载导出文件） | 同 EC2 价格页 |

美东区域的参考估算见 [aws.md 第 2 节](aws.md)（第三方汇总，约 32–35 美元/月），新加坡通常略高。

## 1. 创建 S3 桶（建议）

S3 用来存放每日备份。不建 S3 也能部署，只是只有本机备份，磁盘丢失时无法恢复。

控制台：S3 → 右上角区域选 “Asia Pacific (Singapore) ap-southeast-1” → 创建存储桶：

1. 存储桶名称：`leadworkbench-backup-<你的AWS账号ID>`（全球唯一，小写字母、数字、短横线）。
2. 对象所有权：保持“已禁用 ACL”。
3. **阻止所有公开访问**：保持全部勾选。
4. **存储桶版本控制**：启用。（实例角色有写权限；开启版本控制后，即使旧备份被同名覆盖，旧版本仍在。）
5. 默认加密：**SSE-S3**（Amazon S3 托管密钥）。
6. 创建后进入桶 → 管理 → 创建生命周期规则，建两条：
   - 规则 1 `leadworkbench-expire`：筛选前缀 `leadworkbench/`；勾选“使对象的当前版本过期”30 天、“永久删除对象的非当前版本”非当前 30 天。
   - 规则 2 `leadworkbench-delete-markers`：筛选前缀 `leadworkbench/`；勾选“删除过期的对象删除标记或未完成的分段上传”→“删除过期的对象删除标记”。（AWS 不允许把这一项和“当前版本 N 天后过期”放在同一条规则里，所以分两条。）

可选 CLI。先把下面的内容保存为 `lifecycle.json`：

```json
{
  "Rules": [
    {
      "ID": "leadworkbench-expire",
      "Status": "Enabled",
      "Filter": {"Prefix": "leadworkbench/"},
      "Expiration": {"Days": 30},
      "NoncurrentVersionExpiration": {"NoncurrentDays": 30}
    },
    {
      "ID": "leadworkbench-delete-markers",
      "Status": "Enabled",
      "Filter": {"Prefix": "leadworkbench/"},
      "Expiration": {"ExpiredObjectDeleteMarker": true}
    }
  ]
}
```

```sh
BUCKET=leadworkbench-backup-<你的AWS账号ID>
aws s3api create-bucket --bucket "$BUCKET" --region ap-southeast-1 \
  --create-bucket-configuration LocationConstraint=ap-southeast-1
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration file://lifecycle.json
```

## 2. IAM 角色与实例配置文件

EC2 上的程序通过这个角色上传备份，不需要任何 AWS 密钥。

控制台：IAM → 角色 → 创建角色 → 受信任实体选“AWS 服务”→ 使用案例选 “EC2” → 下一步不勾选任何托管策略 → 角色名 `leadworkbench-ec2` → 创建。然后进入该角色 → 添加权限 → 创建内联策略 → JSON，粘贴下面的**日常策略**（把 `<你的桶名>` 换成第 1 步的桶名）→ 策略名 `leadworkbench-s3-backup`。用控制台创建 EC2 角色时，同名的实例配置文件会自动创建。

日常策略（只允许把备份写到 `leadworkbench/` 前缀下）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "UploadDailyBackups",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::<你的桶名>/leadworkbench/*"
    }
  ]
}
```

**恢复时临时使用的策略**：只有需要从 S3 拉回备份时（第 13 步），才把内联策略改成下面这份，恢复完成后改回日常策略。与 [aws.md 第 3 节](aws.md) 的策略一致：`GetObject` 和 `ListBucket` 必须分成两条写，因为 `s3:prefix` 条件键只出现在列表请求里，和 `GetObject` 写在同一条时下载不会被放行。

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
      "Sid": "RestoreReadOnlyWhenNeeded",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::<你的桶名>/leadworkbench/*"
    },
    {
      "Sid": "RestoreListOnlyWhenNeeded",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<你的桶名>",
      "Condition": {"StringLike": {"s3:prefix": ["leadworkbench/*"]}}
    }
  ]
}
```

不要授予 `DeleteObject`、`PutBucket*`、`GetObjectVersion`。如果以后修改了 `LEAD_BACKUP_S3_PREFIX`，策略里的前缀要同步改。

信任策略（控制台选“EC2”时自动生成；用 CLI 时保存为 `trust.json`）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
```

可选 CLI（日常策略保存为 `policy-daily.json`，恢复策略保存为 `policy-restore.json`）：

```sh
aws iam create-role --role-name leadworkbench-ec2 --assume-role-policy-document file://trust.json
aws iam put-role-policy --role-name leadworkbench-ec2 --policy-name leadworkbench-s3-backup \
  --policy-document file://policy-daily.json
aws iam create-instance-profile --instance-profile-name leadworkbench-ec2
aws iam add-role-to-instance-profile --instance-profile-name leadworkbench-ec2 --role-name leadworkbench-ec2
# 恢复时临时切换（之后用 policy-daily.json 再执行一次改回）：
# aws iam put-role-policy --role-name leadworkbench-ec2 --policy-name leadworkbench-s3-backup --policy-document file://policy-restore.json
```

## 3. 安全组

控制台：EC2 → 区域选新加坡 → 安全组 → 创建安全组，名称 `leadworkbench-sg`，VPC 选默认 VPC。入站规则：

| 类型 | 端口 | 来源 | 用途 |
|---|---|---|---|
| SSH | 22 | `<你的公网IP>/32`（“我的 IP”） | 只允许你自己登录服务器 |
| HTTP | 80 | `0.0.0.0/0` | Let's Encrypt 验证、自动跳转到 HTTPS |
| HTTPS | 443 | `0.0.0.0/0` | 网页访问 |

出站规则保持默认（全部允许）。不要开放其他端口。

可选 CLI：

```sh
VPC_ID=$(aws ec2 describe-vpcs --region ap-southeast-1 --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
SG_ID=$(aws ec2 create-security-group --region ap-southeast-1 --group-name leadworkbench-sg \
  --description "leadworkbench web" --vpc-id "$VPC_ID" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --region ap-southeast-1 --group-id "$SG_ID" --protocol tcp --port 22 --cidr <你的公网IP>/32
aws ec2 authorize-security-group-ingress --region ap-southeast-1 --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --region ap-southeast-1 --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0
echo "$SG_ID"
```

## 4. 创建 EC2 实例

控制台：EC2 → 区域新加坡 → 启动实例：

1. 名称：`leadworkbench`。
2. 应用程序和操作系统映像：**Amazon Linux 2023**，架构选 **64 位（Arm）**。
3. 实例类型：**t4g.medium**。（不要选 x86 的 t3.medium：x86 镜像构建尚未验证。）
4. 密钥对：创建新密钥对，名称 `leadworkbench-key`，类型 ED25519，格式 `.pem`，下载后妥善保存（只能下载一次）。macOS / Linux 上执行 `chmod 400 leadworkbench-key.pem`。
5. 网络设置：选择现有安全组 `leadworkbench-sg`；“自动分配公有 IP”保持启用（之后会换成弹性 IP）。
6. 配置存储：**40 GiB，gp3**；点“高级”展开卷详情：**加密：是**；**终止时删除：否**（实例被终止时磁盘保留）。
7. 高级详细信息：
   - IAM 实例配置文件：`leadworkbench-ec2`；
   - 元数据可访问：已启用；**元数据版本：仅 V2（需要令牌）**；**元数据响应跳数限制：2**（容器要多经一跳才能拿到实例角色凭据，设为 1 时 S3 上传会失败）。
8. 启动实例。

可选 CLI（`<SG_ID>` 用第 3 步的输出）：

```sh
aws ec2 create-key-pair --region ap-southeast-1 --key-name leadworkbench-key --key-type ed25519 \
  --query KeyMaterial --output text > leadworkbench-key.pem
chmod 400 leadworkbench-key.pem
aws ec2 run-instances --region ap-southeast-1 \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --instance-type t4g.medium --key-name leadworkbench-key --security-group-ids <SG_ID> \
  --iam-instance-profile Name=leadworkbench-ec2 \
  --metadata-options HttpEndpoint=enabled,HttpTokens=required,HttpPutResponseHopLimit=2 \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":40,"VolumeType":"gp3","Encrypted":true,"DeleteOnTermination":false}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=leadworkbench}]' \
  --query 'Instances[0].InstanceId' --output text
```

已经建好的实例忘了设元数据选项时，可以补设：`aws ec2 modify-instance-metadata-options --region ap-southeast-1 --instance-id <实例ID> --http-tokens required --http-put-response-hop-limit 2 --http-endpoint enabled`。

## 5. 弹性 IP 与访问主机名

控制台：EC2 → 弹性 IP → 分配弹性 IP 地址（区域新加坡）→ 选中它 → 操作 → 关联弹性 IP 地址 → 实例选 `leadworkbench` → 关联。

**访问主机名**：把弹性 IP 里的点换成短横线，再加 `.sslip.io`。例如弹性 IP 是 `13.250.1.2`，主机名就是 `13-250-1-2.sslip.io`，访问地址是 `https://13-250-1-2.sslip.io`。也可以在任意终端里计算：

```sh
echo "<弹性IP>" | tr . - | sed 's/$/.sslip.io/'
```

后文的 `<主机名>` 都指这个值。弹性 IP 不变，主机名就不变；以后有自己的域名时，见第 14 步“换成自有域名”。

可选 CLI：

```sh
ALLOC_ID=$(aws ec2 allocate-address --region ap-southeast-1 --domain vpc --query AllocationId --output text)
aws ec2 associate-address --region ap-southeast-1 --instance-id <实例ID> --allocation-id "$ALLOC_ID"
aws ec2 describe-addresses --region ap-southeast-1 --allocation-ids "$ALLOC_ID" --query 'Addresses[0].PublicIp' --output text
```

## 6. SSH 登录并下载代码

在你自己的电脑上：

```sh
ssh -i leadworkbench-key.pem ec2-user@<弹性IP>
```

登录到 EC2 后：

```sh
sudo dnf install -y git
cd ~
git clone https://github.com/akubizhang-commits/local-lead-workbench.git
cd ~/local-lead-workbench
git log --oneline -1          # 记下当前部署的版本
```

后续所有服务器命令都在 `~/local-lead-workbench` 目录里执行。

## 7. 部署

两种方式任选其一：7.1 让 kiro-cli 按仓库里的 `deploy/AGENT_RUNBOOK.md` 部署（推荐），或 7.2 手动执行同样的命令。

### 7.1 用 kiro-cli 部署（推荐）

在 EC2 上 `cd ~/local-lead-workbench`，启动 kiro-cli 的对话模式（例如 `kiro-cli chat`），把下面整段提示词粘贴进去。先替换 `<你的桶名>`；没有建 S3 桶就把那一行改成“不使用 S3，LEAD_BACKUP_S3_BUCKET 留空”。

```text
你在一台 AWS 新加坡区域（ap-southeast-1）的 EC2（t4g.medium，Amazon Linux 2023 arm64）上，当前目录是 local-lead-workbench 仓库根目录。
请严格按照 deploy/AGENT_RUNBOOK.md 部署，先完整阅读该文件，并遵守其中第 0 节的全部硬性规则（不改任何 AWS 资源、不读实例角色凭据、不在 .env 或仓库里写密钥、不提交或推送 git、绝不执行 docker compose down -v 或 docker volume rm、不自己跑 Google Maps 查询、不替我设置或查看密码）。
参数：
- LEAD_PUBLIC_HOST：按 runbook 第 1 节从元数据读取公网 IP 后计算（<IP 用短横线连接>.sslip.io），算出后先告诉我，等我确认与弹性 IP 一致再继续。
- LEAD_TLS=acme
- LEAD_BACKUP_S3_BUCKET=<你的桶名>
- LEAD_BACKUP_S3_REGION=ap-southeast-1
执行 runbook 第 1–5 节。docker compose build 首次需要 15–30 分钟，请把命令超时设到至少 45 分钟，或放到后台运行并定期查看输出。
完成第 5 节后停下，不要执行第 6 节，也不要用第 6 节的“备选”做法：告诉我 docker compose ps 的结果、证书方式（acme 或 internal），然后等我自己在 SSH 终端里创建账号。
我回复“账号已创建”后，再执行第 7 节 sh deploy/verify.sh，并按第 8 节向我汇报结果和下一步。
```

kiro-cli 停下来后，按第 8 步自己创建账号，再回到 kiro-cli 回复“账号已创建”。

### 7.2 手动部署（备选）

1. 安装 Docker（并创建 2 GiB swap，t4g.medium 构建镜像时需要）：

   ```sh
   cd ~/local-lead-workbench
   sudo sh deploy/install.sh --swap
   exit
   ```

   脚本会把 `ec2-user` 加入 docker 组，需要**退出后重新 SSH 登录**才生效。重新登录后执行 `cd ~/local-lead-workbench && docker compose version`，能看到版本号就说明生效了。

2. 配置 `.env`（把 `<主机名>`、`<你的桶名>` 换成真实值；不用 S3 时跳过后两条 `sed`）：

   ```sh
   cd ~/local-lead-workbench
   cp -n .env.example .env
   chmod 600 .env
   sed -i 's|^LEAD_PUBLIC_HOST=.*|LEAD_PUBLIC_HOST=<主机名>|' .env
   sed -i 's|^LEAD_TLS=.*|LEAD_TLS=acme|' .env
   sed -i 's|^LEAD_BACKUP_S3_BUCKET=.*|LEAD_BACKUP_S3_BUCKET=<你的桶名>|' .env
   sed -i 's|^LEAD_BACKUP_S3_REGION=.*|LEAD_BACKUP_S3_REGION=ap-southeast-1|' .env
   grep -v '^#' .env | grep .
   ```

   最后一条命令会列出生效的配置，确认 `LEAD_PUBLIC_HOST` 是你的主机名。其余保持默认：`LEAD_TIMEZONE=Asia/Shanghai`、`LEAD_BACKUP_TIME=03:30`、会话空闲 24 小时 / 最长 7 天。**不要在 `.env` 里写任何 AWS 密钥。**

3. 构建并启动（构建首次 15–30 分钟；建议先 `sudo dnf install -y tmux` 并在 `tmux` 里执行，SSH 断开也不会中断）：

   ```sh
   docker compose build
   docker compose up -d
   ```

4. 等 `api`、`worker` 变成 healthy（首次启动要做数据库迁移，最多约 10 分钟，期间网页返回 502 属正常）：

   ```sh
   until docker compose ps worker | grep -q '(healthy)'; do docker compose ps; sleep 15; done
   docker compose ps
   ```

   看到 `api`、`worker` 都是 `(healthy)`、`caddy` 是 `Up` 就可以继续。如果等了 15 分钟还没好，按 Ctrl+C 停止等待，看第 14 步。

5. 检查证书：

   ```sh
   docker compose logs --tail 100 caddy | grep -i 'certificate obtained successfully'
   ```

   有输出即成功。5 分钟内没有成功，见第 14 步“证书申请失败”。

## 8. 创建登录账号

在 SSH 终端里**自己**执行（不要让 AI 助手代为输入密码）：

```sh
cd ~/local-lead-workbench
docker compose exec api python -m leadworkbench.admin create-user <用户名>
```

按提示输入两次密码，屏幕不显示输入内容，成功后显示“已创建账号 <用户名>”。规则：

- 用户名 3–32 个字符，只能包含字母、数字、点、下划线和短横线，以字母或数字开头。
- 密码 12–128 个字符，不能全是空白，不能与用户名相同。连续 3 次不符合要求会放弃，重新执行命令即可。

其他账号命令：

```sh
docker compose exec api python -m leadworkbench.admin list-users
docker compose exec api python -m leadworkbench.admin reset-password <用户名>   # 重置后该账号在所有设备上退出
docker compose exec api python -m leadworkbench.admin disable-user <用户名>
docker compose exec api python -m leadworkbench.admin enable-user <用户名>
docker compose exec api python -m leadworkbench.admin logout-all [<用户名>]     # 不带用户名时所有人退出
```

## 9. 验证

```sh
cd ~/local-lead-workbench
sh deploy/verify.sh
```

期望每一行都是 `PASS`，最后一行是“全部通过。浏览器打开 https://<主机名> 登录……”。脚本不打印密码、Cookie 或凭据，也不调用 AWS。各检查项与 FAIL 的含义：

| 检查 | FAIL 时的含义与处理 |
|---|---|
| `.env` 存在 | 没有 `.env`：执行第 7.2 步第 2 条 |
| `.env 权限 600` | 执行 `chmod 600 .env` |
| `.env 里没有 AWS 密钥` | `.env` 里有 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`：删掉这些行，S3 用实例角色 |
| `LEAD_PUBLIC_HOST=…` | 还是示例值或为空：按第 7.2 步第 2 条设置 |
| `api healthy` / `worker healthy` | 没运行：`docker compose up -d`；`starting`：首次启动等几分钟再试；`unhealthy`：见第 14 步 |
| `caddy running` | `docker compose up -d caddy`，再看 `docker compose logs --tail 100 caddy` |
| `Maps 程序已带稳定性补丁` | 镜像里的 Maps 程序自检失败：重新 `docker compose build` 并 `docker compose up -d`，仍失败把 `docker compose exec worker /app/tools/bin/google-maps-scraper -local-stability-info` 的输出交给开发者 |
| `https://<主机名>/healthz 返回 200` | 外网访问不到：检查安全组 80/443、弹性 IP 是否关联在这台实例、主机名是否与弹性 IP 一致、`docker compose logs caddy` 里的证书错误 |
| `未登录访问 /api/overview 返回 401` | 返回其他值说明访问控制异常，不要继续使用，把输出交给开发者 |
| `http:// 跳转到 https://` | 80 端口不通或 Caddy 未正常启动：检查安全组 80 |
| `已有可用的登录账号` | 还没建账号或账号都被停用：执行第 8 步 |

## 10. 首次登录与确认保留策略

1. 浏览器打开 `https://<主机名>`，用第 8 步的账号登录。
2. 进入“设置”，找到“存储清理”区块（存储保留策略）：默认地图原始结果保留 30 天、自动备份保留最近 10 份、导出文件保留 7 天。确认清单后点确认。**未确认之前任何备份和导出文件都不会被自动清理**，每日备份会一直累积，设置页会提示“存储保留策略尚未确认”。
3. 在“设置 → 备份与恢复”可以看到每日备份时间（03:30，Asia/Shanghai）和 S3 状态（“已配置”）。

## 11. 阶段 0 实测清单

目的：确认云端 Maps 能否在 AWS 新加坡 IP 上使用、客户访问延迟是否可接受、每日 S3 备份是否正常。这些结果决定后续规划（见 [架构设计第 9 节](../architecture.md)）。全部在网页或你本人的电脑上操作，不要让 AI 助手代做。

1. **云端 Maps，第 1 天**：新建一个小市场（例如美国 San Jose、企业类型“批发商”、关键词 `metal roofing supply`），提交前确认预计查询次数，开始检索。**第一个查询单元完成后立即暂停该轮次。** 记录：找到的企业数、用时、有没有出现“验证码”或“访问受限”提示。出现验证码或受限提示就停止，不要重试，也不要点“恢复地图检索”。
2. **云端 Maps，第 2 天**：在大致相同的时段，继续该轮次，再执行 1 个查询单元后暂停，记录同样的内容。
3. **访问延迟**：在客户所在城市的网络下（客户电脑或同城网络），执行 3 次，记录每次的输出：

   ```sh
   curl -o /dev/null -s -w "连接 %{time_connect}s 首字节 %{time_starttransfer}s\n" https://<主机名>/healthz
   ```

   Windows 10/11 自带 `curl.exe`，在 PowerShell 里把 `curl` 写成 `curl.exe`。
4. **S3 备份，第 2 天**：在 S3 控制台打开桶，确认出现 `leadworkbench/<年>/<月>/auto-daily-….sqlite3`；网页“设置 → 备份与恢复”显示“已上传到 S3”。（日常策略没有列表权限，所以在 EC2 上用 `aws s3 ls` 会被拒绝，这是正常的。）

结果表（填好后发回给开发者）：

| 项 | 日期时间 | 结果 | 备注（企业数 / 用时 / 提示原文 / 延迟数值） |
|---|---|---|---|
| Maps 第 1 天 查询单元 1 | | 成功 / 验证码 / 受限 / 其他 | |
| Maps 第 2 天 查询单元 2 | | 成功 / 验证码 / 受限 / 其他 | |
| 延迟 第 1 次（城市：  ） | | 连接 __ s，首字节 __ s | |
| 延迟 第 2 次 | | 连接 __ s，首字节 __ s | |
| 延迟 第 3 次 | | 连接 __ s，首字节 __ s | |
| S3 每日备份 | | 已出现 / 未出现 | 设置页显示的状态 |
| `verify.sh` | | 全部 PASS / 有 FAIL | FAIL 的行 |
| 证书方式 | | acme / internal | |

## 12. 日常运维

### 升级

```sh
cd ~/local-lead-workbench
sh deploy/backup-now.sh        # 先做手动备份，打印备份文件名，记下来
git pull
docker compose build
docker compose up -d
sh deploy/verify.sh
```

数据库迁移只能向前，不能降级。需要回退时，切回旧代码（`git checkout <旧版本>`，重新 build、up），再用升级前的那份备份恢复（第 13 步）。

### 查看状态与日志

```sh
docker compose ps
docker compose logs --tail 200 api
docker compose logs --tail 200 worker
docker compose logs --tail 200 caddy
docker compose exec api ls /data/logs       # 应用 JSON 日志
docker compose restart worker               # 只重启 worker，数据保留
```

### 停止与启动

- 停止服务：`docker compose stop`（或 `docker compose down`，**不带 `-v`**）。
- 停止 EC2 实例（控制台“停止实例”）可以省下实例费用；**EBS 磁盘、弹性 IP、S3 仍然收费**。实例重新启动后，Docker 开机自启，容器按 `restart: unless-stopped` 自动启动，弹性 IP 不变，主机名不变。启动后执行一次 `sh deploy/verify.sh`。
- 停机期间不会做每日备份；启动后 worker 会在约 2 分钟后补做当天错过的备份。
- 不再使用时：先确认 S3 里有最新备份，再终止实例；因为根卷设置了“终止时删除：否”，**磁盘不会随实例删除，需要到 EC2 → 卷 里手动删除**，弹性 IP 也要手动释放，否则持续收费。

> **警告：绝不要执行 `docker compose down -v`，也不要 `docker volume rm`。`-v` 会删除数据卷 `leadworkbench-data`，也就是全部业务数据。**

## 13. 备份与恢复

- 每日备份：每天 03:30（`LEAD_TIMEZONE`，默认 Asia/Shanghai）由 worker 自动创建 `auto-daily-….sqlite3`，配置了桶时上传到 `s3://<你的桶名>/leadworkbench/<年>/<月>/<文件名>`，同一份最多尝试 3 次。错过时间会在 worker 启动 2 分钟后补做，失败 1 小时后重试。
- 手动备份：网页“设置 → 备份与恢复 → 创建备份”，或 `sh deploy/backup-now.sh`。手动备份不上传 S3。
- 恢复不会回滚账号：默认保留当前的账号和登录状态。

### 13.1 网页恢复（常规）

设置 → 备份与恢复 → 选择备份 → 确认。有排队中或运行中的轮次时会被拒绝，先在网页里暂停这些轮次。恢复前系统会自动把当前数据另存为 `auto-pre-restore-…` 备份。

### 13.2 从 S3 拉回备份

1. 把实例角色的内联策略临时换成第 2 步的“恢复时临时使用的策略”。
2. 在 EC2 上执行（Amazon Linux 2023 自带 `aws` 命令，凭据来自实例角色）：

   ```sh
   cd ~/local-lead-workbench
   aws s3 ls s3://<你的桶名>/leadworkbench/2026/10/ --region ap-southeast-1
   aws s3 cp s3://<你的桶名>/leadworkbench/2026/10/<文件名> /tmp/<文件名> --region ap-southeast-1
   docker compose exec -T api sh -c 'umask 077 && cat > /data/backups/<文件名>' < /tmp/<文件名>
   rm /tmp/<文件名>
   ```

   文件名必须保持原名（`auto-daily-….sqlite3`）。api 已停止时，把 `exec -T api` 换成 `run --rm --no-deps -T api`。
3. 在网页里选择该备份恢复（13.1），或用命令行恢复（13.3）。
4. 把内联策略改回日常策略。

### 13.3 命令行恢复（网页打不开、一直提示服务不可用，或提示“无法读取当前账号”）

```sh
cd ~/local-lead-workbench
docker compose stop api worker
docker compose run --rm --no-deps api python -m leadworkbench.admin restore <文件名>
# 提示无法打开当前数据库或无法读取当前账号时，改用备份里的账号：
docker compose run --rm --no-deps api python -m leadworkbench.admin restore <文件名> --accounts backup
docker compose up -d
sh deploy/verify.sh
```

- api、worker 没停时，命令提示“请先停止 api / worker”并退出。
- 默认 `--accounts current` 保留当前账号；当前库里有排队中或运行中的轮次时，命令列出这些轮次并退出：先 `docker compose up -d`，在网页“轮次”中暂停它们，再 `docker compose stop api worker` 后重试；或者改用 `--accounts backup`。
- `--accounts backup`：使用备份里的账号，所有人需要重新登录；备份里没有账号时按提示执行 `create-user`。
- 当前库会先另存到 `backups/auto-pre-restore-…sqlite3`；当前库已损坏时，会原样复制到 `backups/damaged-<时间>/`。

### 13.4 整机丢失

1. 按第 4–7 步新建实例并部署（安全组、角色、桶可以复用；弹性 IP 如果还在就关联到新实例，主机名不变）。
2. 第 8 步创建一个账号。
3. 按 13.2 从 S3 拉回最近的 `auto-daily-…` 备份。
4. 网页恢复（账号保持新建的那个），或命令行 `--accounts backup`（账号回到备份时的状态）。

最多丢失自上次每日备份以来的数据（约 1 天）。旧实例的 EBS 卷如果还在（终止时不删除），也可以联系开发者从卷里取回数据。

## 14. 常见问题排错

**证书申请失败**（`docker compose logs caddy` 里有 acme / challenge 错误，5 分钟内没有 `certificate obtained successfully`）：

1. 确认安全组 80、443 对 `0.0.0.0/0` 开放，`.env` 里的 `LEAD_PUBLIC_HOST` 与当前弹性 IP 一致。
2. 仍失败（例如 Let's Encrypt 限流），改用内部证书：

   ```sh
   sed -i 's|^LEAD_TLS=.*|LEAD_TLS=internal|' .env
   docker compose up -d caddy
   ```

   浏览器会提示证书不安全，需要手动选择继续访问。HSTS 有效期只有 1 天，从 acme 切过来的浏览器最多 1 天后才允许“继续访问”。问题解决后改回 `LEAD_TLS=acme` 再 `docker compose up -d caddy`。

**构建时内存不足**（`signal: killed`、`exit code 137`）：执行 `swapon --show`，确认有 `/swapfile`；没有就 `sudo sh deploy/install.sh --swap`，然后重新 `docker compose build`。

**首次启动网页返回 502**：api 正在做数据库迁移，健康检查最多等 10 分钟，属于正常现象。用 `docker compose ps` 观察，`docker compose logs --tail 100 api` 查看进度。

**api 一直不是 healthy**：

- `docker compose logs --tail 200 api`。看到“配置错误：以下环境变量无效或缺失：…”说明 `.env` 有误，按提示的变量名修改后 `docker compose up -d`。常见原因：`LEAD_PUBLIC_HOST` 仍是示例值；填了 `LEAD_BACKUP_S3_BUCKET` 却没填 `LEAD_BACKUP_S3_REGION`（或反过来）；桶名含大写字母。
- 看到“已有 API 进程在使用该数据目录”：有另一个 api 容器在用同一个数据卷，`docker compose ps -a` 检查后停掉多余的。
- worker 要等 api healthy 后才启动；api 正常、worker unhealthy 时执行 `docker compose restart worker`。

**S3 上传失败**（设置页显示上传失败和错误码）：确认实例已附加 `leadworkbench-ec2` 角色；内联策略里的桶名正确；实例元数据跳数限制为 2（为 1 时容器拿不到凭据）；`.env` 的 `LEAD_BACKUP_S3_REGION=ap-southeast-1` 与桶所在区域一致。改角色或策略不需要重启；改了 `.env` 要执行 `docker compose up -d` 才生效。失败后 worker 每小时重试一次，同一份最多 3 次；3 次都失败就等下一次每日备份。

**Maps 被拦**（网页提示检测到验证码或异常流量，地图检索已暂停）：

- 立即暂停相关轮次，不要反复重试；把提示原文和时间记到第 11 步的结果表里。
- 冷却 30 分钟后，网页“恢复地图检索”按钮才可用；系统不会自动恢复。是否恢复、何时恢复，先与开发者确认。
- 浏览器关闭类错误或空结果会按查询自动重试，不等于被拦。

**无法 SSH 登录**：多数是你的公网 IP 变了。到安全组 `leadworkbench-sg` 把 22 端口的来源改成新的 `<你的公网IP>/32`。其他检查：用户名是 `ec2-user`；密钥文件权限 `chmod 400`；连接的是弹性 IP。

**忘记密码**：管理员在 SSH 终端执行 `docker compose exec api python -m leadworkbench.admin reset-password <用户名>`，按提示输入新密码；该账号在所有设备上退出登录。

**账号被锁**（提示“登录尝试过多”）：同一账号 15 分钟内输错 5 次会被锁 15 分钟。等待，或执行 `docker compose restart api` 清零计数（已登录的会话不受影响）。

**换成自有域名**：DNS 添加 A 记录指向弹性 IP；`.env` 改 `LEAD_PUBLIC_HOST=<你的域名>`；执行 `docker compose up -d`；再 `sh deploy/verify.sh`。

需要把问题交给开发者时，附上 `sh deploy/verify.sh` 的输出、`docker compose ps`，以及相关服务的 `docker compose logs --tail 200 <服务名>`；网页“设置 → 诊断信息”导出的诊断包不含密码、会话和业务数据，也可以一并发送。
