# Seat Assistant

当前版本：`v1.1.2`

Seat Assistant 是一个运行在 Windows 本机的座位预约助手。它通过 Playwright 操作河南理工大学图书馆座位系统，支持预约、结果核验、企业微信通知、动态签到窗口补偿和暂离超时保护。

## 快速开始

以下命令均在项目根目录的 PowerShell 中执行。项目只支持 Windows，要求 Python 3.11 及以上，并安装 Google Chrome。

### 1. 创建环境

`.venv` 不提交到仓库。新电脑下载项目后，在项目根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

程序使用本机 Chrome 的持久化浏览器会话，默认路径为：

```text
C:\Program Files\Google\Chrome\Application\chrome.exe
```

如果 Chrome 安装在其他位置，需要调整代码中的浏览器路径后再运行。

### 2. 创建配置

```powershell
Copy-Item .env.example .env
Copy-Item accounts.example.json accounts.json
notepad .env
notepad accounts.json
```

至少修改以下内容：

- `accounts.json`：填写账号、密码，保留一个 `enabled: true` 的账号，并确认图书馆、阅览室和座位偏好。
- `.env`：设置随机的 `SEAT_CONTROL_TOKEN`。
- 需要企业微信通知时，填写 `SEAT_WECOM_WEBHOOK`。
- 需要企业微信双向控制时，再填写 `SEAT_WECOM_BOT_ID`、`SEAT_WECOM_BOT_SECRET` 和账号的 `wecom_user_id`。
- 需要根据门禁记录判断早到时，填写校准后的 `SEAT_ACCESS_RECORDS_URL`；没有该地址时，迟到补偿和“我的预约”状态判断仍可用，早到识别不可用。

### 3. 初始化账号

初始化只验证登录、座位首页和“我的预约”，不会预约座位。初始化会自动采集图书馆列表，并让你选择座位策略、阅览室和学习窗口：

```powershell
.\.venv\Scripts\python.exe scripts\test_login.py --account my-account
.\.venv\Scripts\python.exe scripts\initialize_account.py --account my-account
```

多账号时把 `my-account` 替换为 `accounts.json` 中的 `id`。例如：`scripts/initialize_account.py --account account03`。只有 `enabled: true` 的账号会运行。

### 4. 手动预约

先预览，不提交真实预约：

```powershell
.\.venv\Scripts\python.exe scripts\preview_reservation.py `
  --account my-account `
  --date "2026-08-30" `
  --start "20:00" `
  --end "22:00"
```

确认页面信息正确后，再提交真实预约：

```powershell
.\.venv\Scripts\python.exe scripts\preview_reservation.py `
  --account my-account `
  --date "2026-08-30" `
  --start "20:00" `
  --end "22:00" `
  --submit `
  --confirm-submit
```

按终端提示输入大写 `SUBMIT`。日期、时间、阅览室和座位必须以实际页面允许的选项为准。

### 5. 自动预约和动态监控

先用演练模式检查配置：

```powershell
.\scripts\install-task.ps1 -DryRun -Python ".\.venv\Scripts\python.exe"
```

确认无误后安装真实预约任务：

```powershell
.\scripts\install-task.ps1 -Python ".\.venv\Scripts\python.exe"
```

#### **修改代码、预约时段配置或动态窗口配置后，需要重新执行上面的命令以更新 Windows 定时任务。**

动态监控也可以手动运行；企业微信机器人由全天机器人任务单独运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_dynamic_monitor --date (Get-Date -Format yyyy-MM-dd)
.\.venv\Scripts\python.exe -m scripts.run_dynamic_monitor `
  --date (Get-Date -Format yyyy-MM-dd) `
  --period morning `
  --run-for-minutes 165
```

## 常用命令

```powershell
# 查看初始化和座位目录
.\.venv\Scripts\python.exe scripts\list_catalog.py --account my-account

# 查看登录页面和接口诊断信息
.\.venv\Scripts\python.exe scripts\diagnose_login.py my-account

# 查看动态监控日志
Get-Content ".\logs\dynamic-monitor-$(Get-Date -Format yyyy-MM-dd).log" -Wait

# 查看自动预约日志
Get-Content ".\logs\scheduled-$(Get-Date -Format yyyy-MM-dd).log" -Wait

# 单独启动企业微信双向机器人（调试或长期运行）
.\.venv\Scripts\python.exe scripts\run_wecom_bot.py

# 手动启动一个时段的动态监控
.\.venv\Scripts\python.exe -m scripts.run_dynamic_monitor --period morning --run-for-minutes 165

# 启动本机控制页
.\.venv\Scripts\python.exe -m seat_assistant.main

# 运行测试
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp

# 卸载 Windows 定时任务
.\scripts\install-task.ps1 -Uninstall
```

静默任务默认安装为真实预约；仅调试时使用 `-DryRun`。手动预约是否提交由 `--submit` 决定。

## 实现介绍

项目按三层协作：

| 层     | 作用                        | 事实来源                   |
| ----- | ------------------------- | ---------------------- |
| 配置层   | 保存账号、学习时段、位置偏好、企业微信和动态参数  | `.env`、`accounts.json` |
| 远端操作层 | 登录、选座、预约、取消、读取“我的预约”和门禁记录 | 学校网页及其 API             |
| 本地状态层 | 保存预约结果、会话进度、命令队列、去重记录和限额  | SQLite 数据库             |

核心原则是：学校网页/API决定现实状态，本地数据库负责记忆和安全控制。数据库不能单独证明远端预约成功或已经入馆。

### 基础预约

- 手动预约支持预览、确认提交、真实提交和结果核验。
- 自动预约由 Windows 计划任务触发，每个账号每次最多执行一个预约任务。
- 选座支持随机空闲座位、指定楼层和具体座位优先。
- 同一账号同一时刻只允许一个有效预约；本地和远端都会在提交前检查。
- 每日成功预约和动态取消默认各限制 15 次。
- 成功、失败和结果不明确都会写入本地数据库；预约结果可通过企业微信 Webhook 推送。

### 初始化和静态预约

初始化会验证登录、座位系统首页和“我的预约”，并把图书馆、阅览室、座位偏好和学习窗口写入 `accounts.json`。验证结果写入 SQLite 的 `account_initialization` 表。

静态自动预约按上午、下午、晚上等配置时段运行。任务使用无头浏览器，不需要一直打开可见页面，但 Windows 用户会话、网络和 Chrome 必须可用。

### 动态窗口补偿

动态监控使用一个共享进程处理启用时段，默认最多 3 个时段。以预约开始时间为锚点：

- 动态窗口覆盖签到窗口前 30 分钟至签到窗口结束后 90 分钟。
- 临近边界提前 2 分钟检查。
- 迟到时每 30 分钟取消原预约，并按“当前”重新预约至原结束时间。
- 动态窗口结束前仍未判断为入馆，取消当前预约，避免形成失约。
- 早于签到窗口入馆时，按当前时间重约一次，然后停止该时段的动态调整。
- “我的预约”中的 `RESERVE`、`CHECK_IN`、`COMPLETE` 等状态会分别归一为已预约、履约中、已履约。
- `awayBegin` 和 `awayEnd` 用于暂离保护：普通时段最长 30 分钟，餐时最长 90 分钟；若暂离截止时间早于预约结束时间，在截止前 2 分钟取消，避免暂离超时违约。

正常查询默认每 180 秒一次，临近动作边界提高到每 120 秒一次。认证失效会关闭旧会话、重建独立会话并指数退避；连续失败后进入安全保持，不自动取消预约。

### 企业微信双向交互

机器人通过官方长连接接收消息，通过账号绑定的 `wecom_user_id` 做预约控制和状态查询权限校验。取消、推迟等涉及网页操作的命令先写入 SQLite 的 `bot_commands` 队列，再由动态监控执行并回传结果；修改默认到馆时间只写入对应账号的 SQLite `defaults` 表，立即生效但不改动当天已有预约；“状态”直接读取本地数据库，“帮助”直接返回命令清单。

机器人收到取消、推迟等修改类命令后会立即回复“已写入本地数据库，等待动态监控受理”；实际取消或调整操作在当天对应动态监控的下一轮执行。

当前远程控制命令：

```text
帮助
状态
今天不去了
取消上午
取消下午
取消晚上
上午推迟到 09:20
下午推迟到 15:20
晚上推迟到 20:20
以后上午默认到馆时间为 09:05
以后下午默认到馆时间为 14:05
以后晚上默认到馆时间为 19:05
```

机器人负责接收、去重和回复；动态监控负责真正取消或调整预约。未绑定发送人不能执行预约控制和状态查询，机器人断线会自动重连。

## 手动预约边界

如果预约是通过学校网页或微信小程序完成，而不是通过本项目的预约流程完成：

- 学校系统中的预约和签到本身不受影响。
- 本地数据库没有预约记录时，手动无界运行的动态监控会判断为无可监测预约并退出；安装的有界动态任务会在运行窗口内按低频间隔继续读取本地数据库，后续产生的预约也会被接入监控。
- 迟到重约、早到重约、动态窗口末端取消和暂离超时取消不会可靠执行。
- 企业微信“状态”可能看不到这条预约；取消命令也可能无法安全定位远端预约，因此不要依赖机器人取消外部手动预约。
- 本地成功预约次数、动态取消次数、座位和时间留档不会自动同步。

最稳妥的方式是使用项目的手动预约流程：浏览器操作仍可人工确认，但程序会同时完成远端核验、数据库记录、通知和动态监控接入。

## 配置与安全

重要配置：

| 配置                                     | 默认值    | 作用            |
| -------------------------------------- | ------:| ------------- |
| `SEAT_DRY_RUN`                         | `true` | 本地服务默认不提交真实预约 |
| `SEAT_DAILY_SUCCESS_LIMIT`             | `15`   | 每日成功预约上限      |
| `SEAT_MAX_CANCEL_PER_DAY`              | `15`   | 每日动态取消上限      |
| `SEAT_DYNAMIC_BEFORE_MINUTES`          | `30`   | 动态窗口提前范围      |
| `SEAT_DYNAMIC_AFTER_MINUTES`           | `90`   | 动态窗口延后范围      |
| `SEAT_DYNAMIC_LATE_RESCHEDULE_MINUTES` | `30`   | 迟到重约颗粒度       |
| `SEAT_DYNAMIC_MAX_PERIODS`             | `3`    | 动态补偿最多启用时段数   |

真实预约前确认：

1. `.env`、`accounts.json`、数据库、浏览器资料和日志没有加入 Git。
2. `SEAT_DRY_RUN`、计划任务的 `-DryRun` 和手动预约的 `--submit` 是三套独立开关。
3. 认证失败、接口返回不明确或无法唯一匹配记录时，程序会保留预约并停止危险操作。
4. 运行动态监控时不要同时启动第二个同账号监控进程。

## 故障排查

- `ModuleNotFoundError`：确认当前目录是项目根目录，并使用 `\.venv\Scripts\python.exe` 启动；必要时重新执行依赖安装命令。
- 登录失败或验证码失败：先运行 `scripts\test_login.py`，再运行 `scripts\diagnose_login.py` 查看页面状态。
- 动态监控显示 `idle`：检查本地数据库是否有当天 `reserved` 记录；外部手动预约不会自动进入本地动态会话。
- 动态监控显示 `error_hold` 或 `safe_hold`：查看动态日志，程序通常会保留当前预约，不要立刻启动第二个实例。
- 早到无法识别：检查 `SEAT_ACCESS_RECORDS_URL` 是否为已校准地址，并确认浏览器页面能读取激活码和门禁记录。
- 企业微信命令无结果：确认机器人进程和动态监控都在运行，发送人已绑定到对应账号，且账号数据库中的 `bot_commands` 状态已处理。

真实网站依赖校园网络、账号权限、验证码和页面/API结构；本地测试不能替代真实预约窗口中的人工确认。
