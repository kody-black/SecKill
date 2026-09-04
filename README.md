
# SecKill

配置驱动的自动化抢单工具。Python 3.11+ / PySide6 / Playwright，支持京东与淘宝。

流程分四个阶段，全部走完才算一次完整抢单：

1. **校时** —— 连接 NTP 服务器取权威时间，UDP 123 被封时改用 HTTP Date 头估算，精度到毫秒
2. **登录** —— 复用保存在本地的登录态，失效时才弹出浏览器扫码
3. **准备** —— 打开商品页、等待加载、按配置选规格
4. **开抢** —— 到点后按配置执行：优先发接口请求，接口未配置时点击页面元素；失败按指数退避重试，次数和总时长都有上限

页面被平台风控拦截时会明确报出命中的特征，方便判断是登录态问题还是频率问题。

## 安装

```powershell
.\seckill.ps1 setup
```

一条命令完成：创建 `.venv`、安装依赖、下载 Chromium 内核（系统装了 Chrome 时内核只作兜底）。

所有日常操作都走这个脚本，`.\seckill.ps1 help` 可随时查看。

## 使用

### 图形界面

```powershell
.\seckill.ps1 run
```

粘贴商品链接会自动识别平台并提取 SKU，设置数量和开抢时间后点「开始抢单」。
首次会弹浏览器要求扫码，登录态保存后长期有效。

### 无界面模式

```powershell
.\seckill.ps1 cli -Url "https://item.jd.com/100014219124.html" -Time "2026-09-10 20:00:00" -Qty 1
```

`Ctrl+C` 随时中止；加 `-Headless` 不弹浏览器窗口。

### 选择器校准

平台页面改版后，用校准工具核对配置：

```powershell
.\seckill.ps1 calibrate -Platform jd -Url "https://item.jd.com/xxxxx.html"
```

工具会打开真实页面，逐条报告 `dom.prepare` / `dom.specs` / `dom.fire` 里每个选择器的命中情况，
未命中时列出页面上真实存在的可点击元素（id、class、文字），直接抄回 YAML 即可。

## 目录结构

```
SecKill
├─ seckill.ps1                 快捷操作脚本（setup/run/cli/test/release…）
├─ main.py                     入口
├─ config/
│  ├─ config.yaml              全局配置
│  └─ platforms/
│     ├─ jd.yaml               京东
│     └─ taobao.yaml           淘宝
├─ seckill/
│  ├─ core/
│  │  ├─ models.py             任务与结果模型
│  │  ├─ events.py             事件总线（核心层无 Qt 依赖）
│  │  ├─ clock.py              NTP / HTTP 校时
│  │  ├─ session.py            登录态管理
│  │  ├─ executor.py           重试引擎：退避、上限、可中断
│  │  └─ scheduler.py          阶段编排：校时 → 登录 → 准备 → 等待 → 开抢
│  ├─ adapters/                平台插件，流程声明在 YAML
│  ├─ drivers/browser.py       Playwright 持久化上下文封装
│  ├─ ui/main_window.py        PySide6 界面
│  └─ cli.py                   无界面模式
├─ tools/calibrate.py          选择器校准工具
├─ tests/                      核心自测 + 端到端测试
└─ release/                    打包产物（不进 Git）
```

核心层不依赖 Qt，`seckill/core/` 里的模块可以单独 import、单独测试，更换界面层时逻辑原样保留。

## 配置

平台流程全部声明在 `config/platforms/*.yaml`，改平台只需改文件。

支持的动作：

| action | 说明 | 关键字段 |
| --- | --- | --- |
| `goto` | 打开地址，支持 `{url}` `{sku}` `{quantity}` 占位符 | `value`, `timeout` |
| `click` | 点击 | `by`, `value`, `timeout` |
| `wait_visible` | 等元素出现 | `by`, `value`, `timeout` |
| `wait_url` | 等 URL 包含某串 | `value`, `timeout` |
| `fill` | 填输入框 | `by`, `value`, `text` |
| `press` | 按键 | `by`, `value`, `key` |
| `js` | 执行 JS | `value` |
| `sleep` | 等待 | `ms` |

定位方式 `by`：`css`（默认）/ `xpath` / `text` / `id` / `role`（形如 `button:提交订单`）。
`optional: true` 的步骤失败只警告不中断，适合「有就点、没有就跳过」的分支。
`dom.success_url` 配置一个 URL 关键字，出现它才算下单成功，失败时会自动截图留证。

### 接口直调

`api` 段配置好后（`enabled: true`）优先走 HTTP 请求，速度快且避开页面渲染，失败自动退回 DOM 点击。
用抓包工具（Chrome DevTools、Fiddler）拿到「加购」和「提交订单」两个真实请求，填进 `api.steps`：

```yaml
api:
  enabled: true
  steps:
    - name: 加入购物车
      request:
        method: GET
        url: "https://cart.jd.com/gate.action"
        params: { pid: "{sku}", pcount: "{quantity}" }
        timeout: 5
      expect:
        status: 200
        not_contains: "商品已售完"
```

`expect` 判断步骤成败：`status` 比对状态码，`contains` / `not_contains` 匹配响应体。
Cookie 会自动从浏览器会话带入，无需手填。

淘宝下单走 mtop 接口，`sign` 签名动态生成，配置需要现抓现用。

## 测试结论

以下是 2026-09 在真实页面上实测的结果，已写进配置：

- **京东商品页对未登录的自动化访问直接返回 403 频控页**（跳转 `pc-frequent-pro.pf.jd.com`，页面提示「暂时无法展示该商品的信息」）。配置里已加 `block_markers` 特征检测，命中时报出明确提示。应对办法：先在界面点「重新扫码登录」建立登录态，再开抢。
- 京东登录页默认展示二维码（`.qrcode-login`），无需切换。
- 京东未登录时页面顶部也有「我的京东」入口，登录判定只认登录后才存在的 Cookie（`thor` / `pin`）。
- 淘宝登录页已升级为 havanaone 统一登录，默认展示密码框，配置里通过 XPath 点击「扫码登录」完成切换。
- 淘宝首页可正常访问，未登录状态下顶部导航含「亲，请登录」。

### 测试覆盖

```bash
PYTHONPATH=. python tests/test_core.py    # 核心逻辑，无需浏览器，秒级
PYTHONPATH=. python tests/test_e2e.py     # 端到端，需要 Chrome，约 10 秒
```

`tests/test_core.py`（8 项）：重试次数有硬上限、停止指令立刻终止循环、退避等待中可被打断、
占位符渲染、NTP 偏移量计算、事件总线监听器异常隔离。

`tests/test_e2e.py`（3 项）基于 `tests/fixtures/mock_shop.html` 模拟商店，
页面会模拟「开抢前按钮不可点、到点才解禁」的真实行为：

| 用例 | 验证内容 |
| --- | --- |
| 定时抢购 | 开抢时刻早于按钮解禁时刻，前几次点击失败并退避，到点后成功下单 |
| 立即抢购 | 按钮已解禁，一次尝试直接走完「抢购 → 订单页 → 提交 → 成功页」 |
| 等待中止 | 300 秒的定时等待在 2 秒内被停止指令打断并正常收尾 |

## 数据目录

运行期数据在 `%LOCALAPPDATA%\SecKill\`（其它平台 `~/.seckill/`），含 `sessions/`（登录态）、
`logs/seckill.log`（轮转日志）、`screenshots/`（失败截图）。设 `SECKILL_HOME` 可改位置。

## 打包

```bash
pyinstaller --noconfirm --clean --name SecKill --windowed --onedir ^
  --add-data "config;config" --add-data "icons;icons" ^
  --distpath release --workpath build --specpath build main.py
```

产物在 `release/SecKill/`，整个目录拷走即可运行；`config/` 放在 exe 同级，
改配置直接编辑，无需重新打包。`release/` 已加入 `.gitignore`。

## TODO

- **交互式页面自适应**：登录后进入商品页时，支持交互式获取当前页面信息，自动定位购买/下单按钮，免去手动校准选择器的步骤。登录页结构相对稳定不需要自适应，重点是商品页和下单页。
- **京东校准**：京东商品页对未登录自动化访问返回 403 频控页，需要先建立登录态再实测校准选择器。当前京东配置沿用的是旧版页面结构，大概率需要更新。

## 风控与合规

- 高频请求会被平台判定为异常。默认退避参数已偏保守，调小会更快被拦。
- 平台官方的「预约 + 开售提醒」在大部分场景下更可靠。
- 连续长时间批量抢单有限制账号的风险。
- 本工具仅供个人学习研究，请勿用于转售、囤货或任何经营性用途。
