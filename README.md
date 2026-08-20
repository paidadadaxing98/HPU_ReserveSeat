# Seat Assistant

本项目是一个本地运行的图书馆座位预约助手，目标是把“每天按习惯预约座位、到馆后刷卡、知道座位位置”变成可调整、可追踪的自动化流程。

项目当前针对焦作市/河南理工大学座位预约系统开发，默认目标为：

- 入口：`https://seatlib.hpu.edu.cn/libseat/`
- 图书馆：`南校区第二图书馆`
- 示例阅览室：`4层计算机类借阅区`（真实 `roomId=34`）
- 本地 Chrome 持久化会话：`.browser-profile`
- 多账号会话目录：`accounts/<账号ID>/browser-profile`（最多 20 个账号）

## 1. 项目目的与需求

### 核心目的

1. 在图书馆开放预约后，按请求预约当天或次日座位；调度器默认在前一天 `19:30` 规划次日座位。
2. 按用户可调整的到馆区间和离馆区间规划学习时段；由于学校账户同一天只能有一个预约，默认每次运行只提交一个时段。
3. 自动登录统一身份认证，进入座位预约系统，选择指定图书馆、阅览室和空闲座位。
4. 在真实提交前保留明确的安全确认，避免误预约、重复提交和违规操作。
5. 允许通过电脑或同一局域网内的手机修改默认到馆时间、推迟某个时段、取消当天预约并查看状态。

### 当前明确的业务边界

- 当天预约窗口为 `07:00-22:30`；次日预约在前一天 `19:30-22:30` 开放。程序只接受当天或次日，不会把更远日期直接提交。
- 预约时间必须按整 30 分钟填写，例如 `08:00`、`15:30`。
- 程序只负责网站预约，不模拟学生卡刷卡、签到、暂离、回馆或结束使用。
- 预约成功后仍需按学校规则到馆签到；离馆时必须在触屏机上正确操作，否则可能产生违规记录。
- 单账号密码从本机 `.env` 读取；多账号密码只从本机 `accounts.json` 读取，不写入日志、README 或提交到代码仓库。
- 多账号时每个账号必须使用独立的会话目录和数据库；账号之间不共享 Cookie、LocalStorage 或预约状态。

## 2. 总体技术路线

```text
本机 Windows 服务
    |
    +-- 19:30 调度器：计算次日预约任务
    |
    +-- 配置与本地数据库：保存默认时段、临时推迟、取消记录、预约结果
    |
    +-- Playwright 浏览器执行器：
    |      统一认证 -> 座位系统 -> 南校区第二图书馆 -> 阅览室 -> 座位 -> 时间
    |
    +-- 规则校验：次日限制、30 分钟粒度、重复预约检查、提交前确认
    |
    +-- 本地控制 API：状态、推迟、修改默认时间、取消当天任务、命令幂等
    |
    +-- 通知层：可选企业微信机器人推送预约结果和异常
```

网站层面采用“浏览器自动化 + 页面接口观察”的方式：浏览器负责登录和页面状态，座位布局、可选开始时间、可选结束时间等信息优先从页面实际请求和 DOM 中读取。这样可以在网站前端变化时及时发现问题，而不是静默提交错误时间。

## 3. 当前默认学习时段

配置位于 `seat_assistant/config.py`，也可以后续迁移到配置文件或手机控制页：

| 时段 | 到馆区间 | 离馆区间 | 默认到馆时间 |
|---|---|---|---|
| 上午 | 08:30-09:30 | 11:30-13:00 | 08:55 |
| 下午 | 14:00-15:00 | 17:30-19:30 | 14:20 |
| 晚上 | 20:00-20:30 | 22:00-22:00 | 20:10 |

“推迟”表示临时有事耽搁，不应被上午 `09:30` 等默认区间强行限制。程序会记录实际推迟时间，用于优化默认到馆时间；最终预约时间仍必须通过网站可选时间和学校规则校验。

## 4. 已完成任务

### 基础工程

- 建立 `seat_assistant` Python 包和测试目录。
- 增加 `.env` 配置读取、SQLite 本地存储、服务层和命令解析。
- 提供 Windows 虚拟环境运行方式和任务计划安装脚本。

### 网站登录与导航

- 使用 `https://seatlib.hpu.edu.cn/libseat/` 作为真实入口。
- 兼容 WebVPN/CAS 跳转，不把 CAS 登录地址错误地当成座位系统入口。
- 支持 `.env` 中的 `SEAT_ACCOUNT`、`SEAT_PASSWORD`。
- 支持 Chrome 持久化会话；已经登录时跳过重复输入。
- 自动选择 `南校区第二图书馆`。
- 自动进入指定阅览室，并校验页面返回的阅览室名称和 ID。

### 座位与预约流程

- 读取 `/rest/v2/room/layoutByDate/{roomId}/{date}` 座位布局接口。
- 识别 `FREE`、`IN_USE`、`AWAY` 和禁用座位。
- 支持按优先级座位号选择，例如 `169 168 170`。
- 选择阅览室前会合并读取网页“我的预约”的分页历史接口 `/rest/v2/history/{page}/{pageSize}` 和当前预约接口 `/rest/v2/user/reservations`；前者用于保留当天全部状态明细，后者在分页接口结构或可用性变化时兜底当前有效预约。记录按网页真实字段 `date`、`begin`、`end`、`loc`、`stat` 解析并去重。当天只要存在 `stat=RESERVE` 的有效预约，就会停止新的预约尝试，不再用时间重合度决定是否允许第二次预约；取消、暂离、已履约、失约、结束使用或缺少有效状态的历史条目会被保留并报告，但不会阻止新的预约流程。
- 读取座位的可选开始时间和结束时间。
- 如果首个优先空闲座位无法覆盖目标时间，会按优先级继续尝试其他空闲座位。
- 开始时间确认后，结束时间接口和页面选项最多按条件等待 30 秒，不再依赖固定 3 秒延时。
- 默认预览模式，不提交真实预约。
- 真实预约需要命令参数 `--submit`；调试时可额外使用 `--confirm-submit`，此时终端会要求输入大写 `SUBMIT`。
- 不带 `--submit` 时只做预览，页面停在“立即预约”前；带 `--submit` 时直接提交，适合已经确认参数和规则的自动化任务。
- 提交前检查指定日期是否已有预约，避免重复提交。
- 提交后尝试进入“我的预约”进行核验；核验不明确时不会自动重试。

### 本地控制与调度

- 本机提供 `8765` 控制接口和简易手机网页。
- 默认只监听 `127.0.0.1`，支持查看状态、推迟上午/下午/晚上、询问推迟时段、修改默认到馆时间、取消时段或取消全天。
- 已固定本地后端接口：`GET /api/v1/health`、`GET /api/v1/status`、`POST /api/v1/commands`。
- 命令请求使用 `Authorization: Bearer <SEAT_CONTROL_TOKEN>` 和 `request_id`；相同 `request_id` 会返回原结果，不会重复执行。
- 服务在每天 `19:30` 按上午、下午、晚上的优先顺序尝试规划次日预约；默认成功或结果不明确后立即停止，当天最多提交一个时段。`SEAT_MAX_RESERVATIONS_PER_RUN` 应保持默认值 `1`，不要改大以免违反学校限制。
- 预约结果可通过企业微信群机器人 Webhook 推送到手机；未配置时保持静默，不影响预约流程。

本地接口示例：

```powershell
$headers = @{ Authorization = "Bearer 你的令牌" }
Invoke-RestMethod http://127.0.0.1:8765/api/v1/health
Invoke-RestMethod http://127.0.0.1:8765/api/v1/status -Headers $headers
Invoke-RestMethod http://127.0.0.1:8765/api/v1/commands -Method Post -Headers $headers `
  -ContentType 'application/json' -Body '{"request_id":"local-1","command":"状态","date":"YYYY-MM-DD"}'
```

当前接口只在本机使用。未来接入企业微信小程序时，云端只转发结构化命令和状态；校园账号、Cookie、浏览器会话仍保留在本地电脑。

### 当前测试

项目已有覆盖配置、命令、日期选择、调度、座位解析、预览、提交保护、本地服务状态和 HTTP 接口的自动化测试。当前本地业务闭环使用 dry-run 适配器验证；真实网站仍需在可访问校园网络且完成统一认证验证码的环境中运行预览，不能仅依据本地测试判断真实页面成功。

## 5. 安装与运行

### 多账号配置

不创建 `accounts.json` 时，程序继续兼容现有的 `SEAT_ACCOUNT`、`SEAT_PASSWORD` 和 `.browser-profile` 单账号模式。需要管理多个账号时，复制 `accounts.example.json` 为本地 `accounts.json`，每个账号填写唯一的 `id`、校园账号和密码：

```json
{
  "accounts": [
    {
      "id": "alice",
      "account": "统一认证账号A",
      "password": "密码A",
      "wecom_webhook": ""
    },
    {
      "id": "bob",
      "account": "统一认证账号B",
      "password": "密码B",
      "wecom_webhook": ""
    }
  ]
}
```

每个账号的浏览器会话和数据库默认分别放在 `accounts/<id>/browser-profile`、`accounts/<id>/seat_assistant.db`。账号配置最多 20 个，账号 ID 和校园账号不能重复。`accounts.json`、`accounts/` 和其中的密码、Cookie、数据库均已加入 Git 忽略。

调度器每天按配置顺序串行执行账号任务，账号之间默认等待 15 秒，不并发访问学校系统。每个账号每天最多记录 3 次“新预约且已核验成功”；失败、登录失败、验证码失败、超时、结果不明确和复用已有预约都不计入成功次数。成功次数按账号和日期隔离保存。

### 配置本机凭据

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少填写：

```dotenv
SEAT_ACCOUNT=你的统一认证账号
SEAT_PASSWORD=你的统一认证密码
SEAT_CONTROL_TOKEN=请设置一段较长的随机令牌
SEAT_DRY_RUN=true
SEAT_WECOM_WEBHOOK=企业微信群机器人的Webhook地址
SEAT_MAX_RESERVATIONS_PER_RUN=1
SEAT_DAILY_SUCCESS_LIMIT=3
SEAT_ACCOUNT_INTERVAL_SECONDS=15
# Optional captcha vision fallback (Qwen compatible API; disabled by default)
SEAT_CAPTCHA_LLM_ENABLED=false
SEAT_CAPTCHA_LLM_API_KEY=
SEAT_CAPTCHA_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
SEAT_CAPTCHA_LLM_MODEL=qwen3.7-flash
SEAT_CAPTCHA_LLM_TIMEOUT_SECONDS=15
SEAT_CAPTCHA_LLM_MAX_ATTEMPTS=2
```

`SEAT_DRY_RUN=true` 是安全默认值，只做演练，不提交真实预约。确认网站流程和时间选择都正确后，才考虑改为 `false`。

`SEAT_WECOM_WEBHOOK` 可选。配置后，实际预约的成功、明确失败或结果不明确会发送一条文本消息到企业微信；核验消息会附带当天捕捉到的预约信息。Webhook 属于敏感凭据，只保存在本机 `.env`，不要粘贴到聊天、日志或 Git 提交中；如果地址曾经暴露，应在企业微信后台删除旧机器人并重新生成。

### 登录与验证码模型

登录流程会先复用对应账号的持久化浏览器会话；会话失效时再填写账号和密码，并确认最终进入 `#/home`，不会把“仍停在登录页”当成成功。检测到验证码后，默认安全停止并报告原因。需要启用千问视觉兜底时，只在本机 `.env` 填写 `SEAT_CAPTCHA_LLM_API_KEY`，再将 `SEAT_CAPTCHA_LLM_ENABLED` 改为 `true`。当前预填模型为 `qwen3.7-flash`，兼容地址为 `https://dashscope.aliyuncs.com/compatible-mode/v1`。

验证码图片只在内存中截图并发送给已配置的视觉接口，不保存图片、不写入日志；模型必须返回严格 JSON，算术验证码只接受 `0-20` 的结果，字母验证码只接受四位英文字母。请求超时、接口异常或答案格式不合规时，本次登录停止，不会快速重复提交。`SEAT_CAPTCHA_LLM_MAX_ATTEMPTS` 只能设置为 `1-3`，默认 `2`。

本地验证通知链路时，可先保持 `SEAT_DRY_RUN=true`，填写新的 Webhook 后运行：

```powershell
.\.venv\Scripts\python.exe -c "from seat_assistant.main import build_service; from seat_assistant.scheduler import run_once; s, service = build_service('alice'); print(run_once(service, 'YYYY-MM-DD'))"
```

该命令会执行指定账号的本地 dry-run 预约并发送通知，不会向学校网站提交真实预约。将 `alice` 和 `YYYY-MM-DD` 替换为实际账号 ID 和演练日期。单账号兼容模式可以省略账号参数：`build_service()`。

### 启动本地服务

```powershell
.\.venv\Scripts\python.exe -m seat_assistant.main
```

启动后：

- 电脑访问：`http://127.0.0.1:8765/?token=你的令牌`
- 手机访问：把 `127.0.0.1` 换成运行程序电脑的局域网 IPv4 地址；手机和电脑需连接同一 Wi-Fi。
- 默认只监听 `127.0.0.1`。如临时设置 `SEAT_CONTROL_HOST=0.0.0.0`，它只表示监听本机网卡，**不等于给程序分配公网 IP**；不要把控制端口直接暴露到公网。

### 单次预览/真实提交

```powershell
.\.venv\Scripts\python.exe scripts\preview_reservation.py `
  --room "4层计算机类借阅区" `
  --date "YYYY-MM-DD" `
  --start "09:00" `
  --end "12:00" `
  --preferred 169 168 170
```

使用 `accounts.json` 时，在命令中追加 `--account "alice"` 选择账号；未配置多账号时不要添加该参数，程序会继续使用 `.env` 的单账号会话。

如需重新采集结束时间接口（不提交预约），运行：

```powershell
.\.venv\Scripts\python.exe scripts\capture_end_times.py `
  --room "4层计算机类借阅区" `
  --date "YYYY-MM-DD"
```

多账号采集时追加 `--account "alice"`，以使用该账号独立的浏览器会话。

采集脚本会等待你在页面中点击一个空闲座位和开始时间，然后保存脱敏后的原生请求路径、查询参数名称、响应状态和结束时间选项到 `end-time-capture.json`。它不会点击“立即预约”。

不带 `--submit` 时脚本只预览并停在“立即预约”前；加上 `--submit` 会直接提交。调试阶段如果希望保留人工护栏，可使用 `--submit --confirm-submit`，并在终端输入大写 `SUBMIT`。测试时应先确认页面实际显示的日期、开始时间、结束时间和座位号。

使用 `--submit` 完成真实预约后，脚本会捕捉提交成功/失败页面提示，定位成功弹窗自身的关闭控件，并等待成功文字、外层容器和遮罩层全部隐藏后，再进入“我的预约”核验；如果阻塞层仍可见，程序会停止导航，避免卡住或发送误导通知。核验会合并当天历史和当前预约记录，只有完全匹配的 `RESERVE` 记录才判定成功，并发送企业微信通知。预约成功弹窗自动关闭和千问验证码登录都已经完成真实链路验证；定时器接入真实预约适配器仍属于 v0.xx 的未闭环问题。若当天已有其他有效预约，会明确报告并停止；如果提交超时或预约接口迟迟没有匹配记录，也会发送结果不明确提醒。未加 `--submit` 的预览不会发送通知。

为减少不必要的取消和重新预约，脚本在进入阅览室前及点击“立即预约”前都会读取当天全部预约记录。请求 `15:30-17:00` 时，只要当天已有 `20:00-21:00` 等 `RESERVE` 预约，就会直接停止，不再进入选座和提交；取消、过期或无效状态的条目只会出现在当天记录报告中。

## 6. 校准真实网站

```powershell
.\.venv\Scripts\python.exe scripts\calibrate.py
```

多账号校准时追加 `--account "alice"`，不要让不同账号共用浏览器会话。

校准脚本用于采集页面选择器和关键接口，不会提交预约或取消预约。按终端提示依次完成登录、进入座位预约首页、选择图书馆、进入阅览室、打开座位时间弹窗。生成的 `site-calibration.json` 可能包含站点结构和响应数据，不应上传到 GitHub。

如果直接进入 `#/home`，说明浏览器持久化会话已经登录；如果停在 `#/login`，应先确认入口仍为 `/libseat/`，再检查本地会话或 `.env` 凭据。

## 7. 已知限制与风险

登录自动化已经完成真实页面验证：会话失效时可填写账号密码、截图验证码、调用千问并确认最终进入 `#/home`。本地 OCR 尚未接入，因此依赖外部模型配置；识别失败时仍会安全停止。多账号配置、独立会话、串行调度和每日成功额度已经落地；在真实预约适配器接入主服务前，仍不应无人值守开启真实预约。

主服务的定时调度已支持多账号 dry-run 和配额控制；已验证的真实网站提交仍由 `scripts/preview_reservation.py` 执行。将该真实流程收敛为主服务适配器、再开启无人值守定时提交，是后续必须完成的工作。

其他限制：

- 座位可能在读取布局后被其他人抢先预约；当前应停止并报告，而不是盲目重复提交。
- 网站前端、接口字段、登录跳转或预约弹窗变化时，需要重新校准。
- 目前还没有完整的自动换座策略和失败重试策略。
- 当前手机控制页和 `/api/v1` 接口是本地服务，不是公网服务；企业微信机器人通知目前只支持单向推送，还没有小程序双向消息通道。
- 程序无法替代现场刷卡和签到，违规责任仍由使用者承担。

## 8. 下一步待开发

### P0：完成可靠预约闭环

1. 在当前预约窗口重新运行 `scripts/capture_end_times.py`，确认当天和次日的原生结束时间请求。
2. 增加时间控件调试信息：文本、坐标、class、可见性、禁用状态、选中状态和截图。
3. 提交前再次读取页面实际选中日期、开始时间、结束时间，任何不一致立即停止。
4. 完善预约成功/失败/已占用/重复预约的明确结果识别。

### P1：无人值守与日常使用

1. 按前一天 `19:30` 自动执行次日预约，并支持 Windows 任务计划可靠启动。
2. 增加空闲座位候选队列和抢占失败后的有限换座。
3. 将当前企业微信机器人通知扩展为企业微信小程序订阅消息或云端中继。
4. 支持通过手机修改默认到馆时间、临时推迟时间、取消当天某个时段或全天，并记录变更历史。
5. 根据实际到馆记录优化默认到馆时间，但不自动修改学校规定的预约和签到边界。

### P2：云端控制与企业微信小程序

1. 将现有 `/api/v1` 契约接到云端中继：云端只保存指令和状态，本地程序主动拉取并访问校园系统。
2. 增加设备认证、HTTPS、令牌轮换、最小权限和审计日志，避免暴露校园账号和控制端口。
3. 创建企业微信小程序客户端，先实现状态、推迟、取消和默认时间修改，不改变本地业务规则。
4. 模型只负责意图识别；日期、时间粒度、重复预约、提交确认等最终校验必须由本地规则完成。

## 9. 开发与验证命令

```powershell
# 完整测试
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp

# 编译检查
.\.venv\Scripts\python.exe -m compileall -q scripts seat_assistant

# 诊断登录与页面状态
.\.venv\Scripts\python.exe scripts\diagnose_login.py
# 多账号诊断：
.\.venv\Scripts\python.exe scripts\diagnose_login.py alice

# 只验证自动登录，不选择阅览室、座位或提交预约
.\.venv\Scripts\python.exe scripts\test_login.py
# 多账号登录验证：
.\.venv\Scripts\python.exe scripts\test_login.py --account alice
```

如果直接运行 `python -m pytest -q`，pytest 会把临时目录放在系统 Temp；当该目录存在残留或被其他 pytest 进程同时扫描时，可能在 `tmp_path` 初始化阶段报错。使用项目内的 `--basetemp .pytest-tmp` 可以绕开这类环境问题；这类报错不代表业务测试断言失败。

真实预约前建议顺序：先跑自动化测试，再运行预览模式，确认页面上显示的日期/时间/座位；人工调试使用 `--submit --confirm-submit`，确认自动化运行稳定后再使用 `--submit`。

## 10. 安全说明

- `.env`、`accounts.json`、`.browser-profile`、`accounts/`、`site-calibration.json`、`seat_assistant.db` 都属于本地敏感数据，不应提交到公共仓库。
- 不要在聊天、截图或日志中公开 CAS token、Cookie、账号密码。
- 不要把 `8765` 端口直接映射到公网；需要远程访问时，应使用经过认证和加密的中继服务。
- 自动化只能辅助预约，不能规避学校签到、暂离和黑名单规则。
