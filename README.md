# Seat Assistant

当前版本：`v0.9.1`

本项目在 Windows 本机运行，通过 Playwright 登录河南理工大学图书馆座位系统，支持账号初始化、手动预约和无感定时预约。

## 快速开始

### 1. 添加新账号

账号命令使用的是 `accounts.json` 中的 `id`，不是学号。编辑项目根目录的 `accounts.json`：

```json
{
  "id": "account03",
  "enabled": true,
  "account": "统一认证账号",
  "password": "统一认证密码",
  "wecom_webhook": ""
}
```

只有 `enabled: true` 的账号会被加载、登录和预约。禁用账号不会校验账号密码，也不会创建会话：

```json
{
  "id": "account04",
  "enabled": false,
  "account": "以后再启用的账号",
  "password": "以后再启用的密码"
}
```

账号 ID 必须唯一。多账号时命令必须带 `--account account03`。

### 2. 初始化账号

先测试登录：

```powershell
.\.venv\Scripts\python.exe scripts/test_login.py --account account03
```

需要查看登录页面时使用：

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_login.py account03
```

执行初始化：

```powershell
.\.venv\Scripts\python.exe scripts/initialize_account.py --account account03
```

初始化只验证登录、座位系统首页和“我的预约”接口，不预约座位。它会把位置、座位偏好和学习窗口写回 `accounts.json`，并把初始化状态写入 `accounts/account03/seat_assistant.db`。
初始化过程中会自动采集并按编号显示三个图书馆各自的阅览室，后续 `--seat 图书馆编号-阅览室编号-座位号` 按这次显示的编号填写。

只想查看编号、不修改初始化配置时，使用：

```powershell
.\.venv\Scripts\python.exe scripts/list_catalog.py --account account03
```

这个命令会重新登录并输出全部图书馆和阅览室，例如：

```text
图书馆 1. 南校区第一图书馆
  1. ...
图书馆 2. 南校区第二图书馆
  1. ...
图书馆 3. 北校区图书馆
  1. ...
```

阅览室编号是当前页面动态采集的结果，不能把某个图书馆的编号用于另一个图书馆。学校调整目录后，请重新执行该命令。

初始化交互顺序：

1. 自动采集图书馆列表并选择图书馆，无需手动先点击下拉框。
2. 选择座位策略：随机空闲座位、指定楼层内随机、具体座位优先。
3. 具体座位优先时，选择阅览室，再输入座位号，例如 `23 45 85`。
4. 设置 morning、afternoon、evening 学习窗口；直接回车保留默认值。

图书馆编号由当前页面动态采集，通常为：

| 图书馆编号 | 图书馆      |
| -----:| -------- |
| 1     | 南校区第一图书馆 |
| 2     | 南校区第二图书馆 |
| 3     | 北校区图书馆   |

| 阅览室编号 | 第二图书馆         | 第一图书馆       | 北图书馆   |
| -----:| ------------- | ----------- | ------ |
| 1     | 1层自主学习空间（Ⅰ）   | 中国文学类借阅区    | 负一楼自习室 |
| 2     | 2层报刊阅览区       | 外国文学类借阅区    | 一楼自习室  |
| 3     | 3F多媒体信息共享空间   | 文学综合类借阅区    | 三楼自习室A |
| 4     | 3层自主学习空间（Ⅱ）   | 朗读亭         | 三楼自习室B |
| 5     | 3层自主学习空间（Ⅲ）   | 三楼天井区       |        |
| 6     | 4层工程技术类借阅区    | 三楼自主学习空间（Ⅰ） |        |
| 7     | 4层计算机类借阅区     | 四楼多媒体       |        |
| 8     | 5层外文图书原版借阅区   | 四楼天井区       |        |
| 9     | 5层工程技术类借阅区    | 五楼天井区       |        |
| 10    | 5层自然科学借阅区     | 五楼自主学习空间（Ⅲ） |        |
| 11    | 6层社会科学借阅区（Ⅰ）  | 五楼自习室       |        |
| 12    | 6层社会科学类借阅区（Ⅱ） | 六楼自习室       |        |
| 13    | 7层社会科学类借阅区1   |             |        |
| 14    | 7层社会科学类借阅区2   |             |        |
| 15    | 7层自主学习空间（V）   |             |        |
| 16    | 7层自主学习空间（Ⅳ）   |             |        |

例如“南校区第二图书馆 -> 5层自然科学借阅区 -> 座位 23、45、85”：

```powershell
.\.venv\Scripts\python.exe scripts/initialize_account.py `
  --account account03 `
  --seat 2-10-23 `
  --seat 2-10-45 `
  --seat 2-10-85 `
  --time 09:00-10:00 14:30-18:30 19:30-22:00
```

`--seat` 格式是 `图书馆编号-阅览室编号-座位号`：

| 写法        | 含义                           |
| --------- | ---------------------------- |
| `2-x-x`   | 第二图书馆内随机阅览室、随机空闲座位           |
| `2-10-x`  | 固定第二图书馆第 10 个阅览室，随机空闲座位      |
| `2-10-23` | 固定第二图书馆第 10 个阅览室，优先尝试 23 号座位 |

可以重复使用 `--seat`。规则越具体越优先，`x` 表示该级别自动选择。

时间窗口也可以通过命令行设置：

```powershell
# 依次对应 morning、afternoon、evening；x 表示不修改
.\.venv\Scripts\python.exe scripts/initialize_account.py `
  --account account03 `
  --time 09:00-10:00 x 19:30-22:00
```

查看全部参数：

```powershell
.\.venv\Scripts\python.exe scripts/initialize_account.py --help
```

### 3. 手动预约：演练、确认提交、直接提交

日期只能填写当前允许预约的当天或次日，时间必须按 30 分钟填写，并位于账号学习窗口内。

只演练、不提交：不加 `--submit`。浏览器会打开并停在“立即预约”前：

```powershell
.\.venv\Scripts\python.exe scripts/preview_reservation.py `
  --account account03 `
  --date "2026-08-22" `
  --start "20:00" `
  --end "21:30" `
  --preferred 23 45 85
```

调试真实提交：加 `--submit --confirm-submit`。页面选好后，终端会要求输入大写 `SUBMIT`：

```powershell
.\.venv\Scripts\python.exe scripts/preview_reservation.py `
  --account account03 `
  --date "2026-08-22" `
  --start "20:00" `
  --end "21:30" `
  --preferred 23 45 85 `
  --submit `
  --confirm-submit
```

确认页面中的图书馆、阅览室、座位、日期和时间都正确后输入 `SUBMIT`。直接回车不提交。

确认无误后自动真实提交：

```powershell
.\.venv\Scripts\python.exe scripts/preview_reservation.py `
  --account account03 `
  --date "2026-08-22" `
  --start "20:00" `
  --end "21:30" `
  --preferred 23 45 85 `
  --submit
```

`--room` 和 `--preferred` 是本次命令的临时覆盖项；省略它们时使用初始化保存的位置和座位偏好。`--room` 不单独指定图书馆，图书馆来自账号初始化配置。

### 4. 安装静默定时任务

默认安装为真实提交模式：

```powershell
.\scripts\install-task.ps1
```

当前安装脚本默认参数：

| 参数               | 默认值     | 作用            |
| ---------------- | -------:| ------------- |
| `-MorningAt`     | `22:05` | 前一天开始检查次日上午预约 |
| `-AfternoonAt`   | `12:30` | 当天开始检查下午预约    |
| `-EveningAt`     | `19:10` | 当天开始检查晚上预约    |
| `-RepeatMinutes` | `10`    | 任务触发间隔        |

修改时间后重新安装：

```powershell
.\scripts\install-task.ps1 `
  -MorningAt "22:05" `
  -AfternoonAt "12:30" `
  -EveningAt "19:10" `
  -RepeatMinutes 10
```

静默任务默认强制使用真实预约，不受 `.env` 中 `SEAT_DRY_RUN` 影响。需要调试计划任务但不提交真实预约时，重新安装为演练模式：

```powershell
.\scripts\install-task.ps1 -DryRun
```

演练确认无误后，重新安装真实模式：

```powershell
.\scripts\install-task.ps1
```

查看任务和最近一次执行结果：

```powershell
Get-ScheduledTask -TaskName `
  "SeatAssistant-Morning","SeatAssistant-Afternoon","SeatAssistant-Evening"
Get-ScheduledTaskInfo -TaskName "SeatAssistant-Evening"
```

按任务查看、修改、测试和恢复：

```powershell
# 查看
Get-ScheduledTask -TaskName "SeatAssistant-Morning"
Get-ScheduledTaskInfo -TaskName "SeatAssistant-Morning"
Get-ScheduledTask -TaskName "SeatAssistant-Afternoon"
Get-ScheduledTaskInfo -TaskName "SeatAssistant-Afternoon"
Get-ScheduledTask -TaskName "SeatAssistant-Evening"
Get-ScheduledTaskInfo -TaskName "SeatAssistant-Evening"

# 修改
.\scripts\install-task.ps1 -MorningAt "22:05" -AfternoonAt "12:30" -EveningAt "19:10" -RepeatMinutes 10
.\scripts\install-task.ps1 -MorningAt "22:10" -AfternoonAt "12:35" -EveningAt "19:15" -RepeatMinutes 15

# 测试
Start-ScheduledTask -TaskName "SeatAssistant-Morning"
Start-ScheduledTask -TaskName "SeatAssistant-Afternoon"
Start-ScheduledTask -TaskName "SeatAssistant-Evening"

# 恢复
.\scripts\install-task.ps1
```

也可以绕过 Windows 计划任务，直接手动执行同一套静默入口：

```powershell
# 演练，不提交真实预约
.\.venv\Scripts\python.exe -m scripts.run_scheduled_task --period evening --dry-run

# 真实提交，使用账号初始化保存的配置
.\.venv\Scripts\python.exe -m scripts.run_scheduled_task --period evening
```

定时任务演练会完整运行账号初始化检查、预约流程和通知流程，但结果只标记为 `dry-run`：不会提交真实预约，不写入 `reservations` 的 `reserved` 记录，也不会增加每日成功预约次数。`--dry-run` 会强制使用演练适配器，不受 `.env` 中 `SEAT_DRY_RUN=false` 影响。真实提交不要加 `--dry-run`，并确认计划任务已用不带 `-DryRun` 的命令重新安装。

当前静默任务使用无头浏览器，不弹出窗口、不抢占桌面；每次 Python 进程执行一轮后退出。定时任务不会启动企业微信智能机器人；机器人仍保留为独立扩展入口，需要时单独运行。定时任务只发送一条账号汇总通知，不再同时发送每个预约结果明细。电脑可以锁屏或睡眠，但必须保持 Windows 用户会话有效、联网且不能关机或注销。日志位于：

```powershell
Get-Content ".\logs\scheduled-$(Get-Date -Format yyyy-MM-dd).log" -Wait
```

卸载任务：

```powershell
.\scripts\install-task.ps1 -Uninstall
```

## 配置和业务规则

默认学习窗口：

| 时段        | 默认窗口          | 默认预约开始  | 默认结束    |
| --------- | ------------- | -------:| -------:|
| morning   | `08:00-12:00` | `08:30` | `12:00` |
| afternoon | `14:30-18:30` | `15:00` | `18:30` |
| evening   | `19:30-22:00` | `20:00` | `22:00` |

学校同一账号同一时刻只能有一个生效预约。因此每个计划任务每个账号最多提交一个时段；前一个预约未结束时返回 `waiting`，结束后后续计划任务再继续。每天最多成功预约 5 次，默认启用 3 个时段。

程序只负责预约，不负责刷卡签到、暂离、回馆或结束使用。预约成功后仍需遵守学校签到和入馆规则。

安全默认配置位于 `.env`：

```dotenv
SEAT_DRY_RUN=true
SEAT_WECOM_WEBHOOK=
SEAT_WECOM_BOT_ID=
SEAT_WECOM_BOT_SECRET=
SEAT_WECOM_BOT_WS_URL=wss://openws.work.weixin.qq.com
SEAT_WECOM_BOT_DEFAULT_USER=
SEAT_WECOM_BOT_LOCK_FILE=logs/wecom-bot.lock
```

`SEAT_DRY_RUN=true` 影响本地服务和相关演练配置；手动 `preview_reservation.py` 是否提交以 `--submit` 为准；静默任务是否演练以安装时的 `-DryRun` 为准。

账号密码、Webhook、Cookie、浏览器目录、数据库和日志只保存在本机。


### 5. 企业微信智能机器人长连接

企业微信智能机器人长连接是独立常驻进程，不会改变现有预约服务和 Windows 定时任务。先在 `.env` 配置机器人参数：

```dotenv
SEAT_WECOM_BOT_ID=企业微信智能机器人 Bot ID
SEAT_WECOM_BOT_SECRET=企业微信智能机器人 Secret
SEAT_WECOM_BOT_WS_URL=wss://openws.work.weixin.qq.com
SEAT_WECOM_BOT_LOCK_FILE=logs/wecom-bot.lock
```

再在 `accounts.json` 中给账号配置一对一接收人和别名：

```json
"wecom_user_id": "企业微信用户 ID",

"wecom_aliases": ["account03", "张三"]
```

启动长连接机器人：

```powershell
.\.venv\Scripts\python.exe scripts/run_wecom_bot.py
```

当前支持的推文命令：

```text
推文 account03 标题 | https://example.test/a
推文 @张三 标题 | https://example.test/a | 备注
```

机器人会按消息 ID 去重，断线后自动退避重连，并用 `SEAT_WECOM_BOT_LOCK_FILE` 防止同一台机器重复启动。收到消息后优先使用企业微信回调提供的 `response_url` 回复当前发起人；因此在群里发送命令时，回复会回到当前群会话。跨用户主动私发需要企业微信侧提供对应的主动发送能力，不能仅依赖普通群机器人 Webhook。

## 常用辅助命令

```powershell
# 重置某个账号当天的本地预约状态，恢复为“未执行”
# 只清理 reservations、successful_bookings、scheduler_runs
# 不会删除初始化状态、座位偏好、时间窗口和默认到馆时间
.\.venv\Scripts\python.exe scripts/reset_day.py `
  --account account03 `
  --date "2026-08-22" `
  --yes

# 重新采集指定阅览室的结束时间
.\.venv\Scripts\python.exe scripts/capture_end_times.py `
  --account account03 `
  --room "5层自然科学借阅区" `
  --date "2026-08-22"

# 查看命令帮助
.\.venv\Scripts\python.exe scripts/preview_reservation.py --help

# 自动化测试
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp
```

真实网站流程仍依赖校园网络、登录状态、验证码和页面结构；本地测试不能替代真实预约窗口中的预览验证。
