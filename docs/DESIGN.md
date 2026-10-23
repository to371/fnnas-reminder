# 定时提醒 · 设计文档

> 面向贡献者的设计说明：需求边界、数据模型、调度算法、通道抽象、接口清单与决策记录。
> 用户向文档见 [README](../README.md)。

| 项 | 内容 |
| --- | --- |
| 项目代号 | fnnas-reminder（应用包名 `fnnas.reminder`） |
| 文档版本 | v1.3.2（消息模板 + 换行修复 + 开源加固） |
| 编写日期 | 2026-10-23 |
| 目标平台 | 飞牛 fnOS（Debian 内核），x86_64 |
| 形态 | fpk 原生应用包，依赖 fnOS 的 `python312` 运行时 |
| 当前状态 | 功能已完成并开源；72 项单测 + 57 项端到端检查全绿；已在 x86_64 真机安装验证 |

> **v1.2.2 修复**：`/api/rules/:id/test` 曾返回全局最近 4 条日志，导致「试发」把别的规则的历史
> 邮件失败显示成本条提醒的失败；改为返回本次 `results`。同时未配置的通道一律 `skipped`（不连网、不重试、
> 不算失败），只有配好却发不出去才是 `failed`。
>
> **v1.2.1 修复**：页面被 iframe 嵌入时（应用中心 / 预览面板）浏览器拦截原生 `confirm()`/`prompt()`，
> 导致「点删除没反应」「点稍后没反应」；改为页内弹层，并加静态扫描测试防回归。
>
> **v1.2 变更摘要**：应用名由「飞牛 · 定时提醒」统一为「**定时提醒**」；通知通道从
> 「1 个钉钉机器人」扩展为「**钉钉群机器人1 / 机器人2 + 邮件**」三种，每条提醒可自选组合；
> 界面背景由纯白改为浅色渐变。
>
> **v1.1 变更摘要**：按评审意见修复了时间计算的 `timedelta.seconds` 截断缺陷，补齐时区绑定、
> 并发模型、SQLite 索引与事务、snooze 落库、一次性规则完成态、通道适配器抽象与可选口令。
> 逐条处置见 [附录 B](#附录-b评审意见处置对照表)。

---

## 1. 项目概述

### 1.1 背景

飞牛 NAS 通常是 7×24 在线的家用/小型办公设备，天然适合承担"个人提醒中枢"的角色：不依赖手机常驻、不依赖第三方云服务、数据留在自己硬盘上。但 fnOS 自带功能中没有通用的定时提醒组件，应用中心里的第三方应用也少有"按时段周期提醒 + 多通道推送"的组合。

### 1.2 目标

> **在指定的时间规则下，按选定的通道（钉钉群机器人 / 邮件）把提醒内容推出去，并留下可查的执行记录。**

必须覆盖的典型用法：

1. 工作日 09:00–18:00，每小时提醒一次"起来喝水 / 活动一下"；
2. 每天 09:30 提醒"提交日报"，每周五 17:30 提醒"写周报"；
3. 一次性提醒："10 月 30 日 20:00 交电费"；
4. 从某时刻起每 90 分钟提醒一次（如服药、久坐）；
5. 所有提醒可通过钉钉群机器人推到手机，重要提醒同时发邮件留档。

### 1.3 非目标（本期明确不做）

- 不做日历同步（CalDAV / 飞书 / 腾讯日历双向同步）；
- 不做多人协作、共享提醒、提醒指派；
- 不做手机 App、不做微信小程序；
- 不做自然语言解析；
- 不做语音/电话通知；
- 不做公网 SaaS 化与多租户；
- 不做完整账号体系（默认内网免密，可选单口令）。

### 1.4 成功标准

| 编号 | 标准 | 验收方式 |
| --- | --- | --- |
| S1 | 规则到点后 30 秒内完成推送 | 日志时间戳比对 |
| S2 | 断电重启后规则不丢、不重复轰炸 | 重启演练 |
| S3 | 钉钉、邮件各有独立测试按钮且返回可读结果 | 手工点测 |
| S4 | 从零到跑起来 ≤ 10 分钟 | 新人试用 |
| S5 | 内存占用 < 100MB，CPU 常驻 < 1% | 系统监控 |
| S6 | 异常可定位（日志含失败原因原文） | 日志审查 |

---

## 2. 用户与场景

### 2.1 角色

| 角色 | 说明 | 关注点 |
| --- | --- | --- |
| 使用者（本人） | 部署在自己 NAS 上的家庭/个人用户 | 配置简单、不漏提醒、不要半夜响 |
| 家庭其他成员 | 只接收提醒，不配置 | 消息清晰、别刷屏 |
| 开发者（可选） | 想把应用发给别人用 | 打包规范、可升级、可回滚 |

### 2.2 用户故事

- **US-1（P0）** "工作日 9 点到 18 点每小时提醒我喝一次水"。
- **US-2（P0）** 提醒同时发钉钉群和邮箱，一个即时一个留档。
- **US-3（P0）** 随时暂停某条提醒而不删除。
- **US-4（P0）** 看到"下次什么时候响"与历史失败记录。
- **US-5（P1）** 收到提醒后可"稍后 15 分钟"，而不改规则。
- **US-6（P1）** 免打扰时段，避免半夜被吵。
- **US-7（P1）** 导出/导入规则，换机器不用重配。
- **US-8（P2）** 每月 1 号提醒交房租。
- **US-9（P1）** 打成 `.fpk` 装进应用中心，有图标、能启停。

---

## 3. 功能需求

### 3.1 功能清单

| 编号 | 模块 | 功能 | 优先级 | 状态 |
| --- | --- | --- | --- | --- |
| F-01 | 规则 | 一次性提醒 | P0 | 已实现 |
| F-02 | 规则 | 每日定点（1..N 个时刻） | P0 | 已实现 |
| F-03 | 规则 | 每周定点（星期 + 时刻） | P0 | 已实现 |
| F-04 | 规则 | **每日时段内周期提醒**（可跨零点、可限星期） | P0 | 已实现 |
| F-05 | 规则 | 固定间隔（锚点起每 N 分钟） | P1 | 已实现 |
| F-06 | 规则 | 每月某日 | P2 | 未实现 |
| F-07 | 规则 | 增删改查、暂停/启用 | P0 | 已实现 |
| F-08 | 规则 | 立即试发（不改动下次触发） | P0 | 已实现 |
| F-09 | 规则 | 跳过本次 | P1 | 已实现 |
| F-10 | 规则 | 稍后提醒 snooze（5/15/30/60 分钟…） | P1 | 已实现 |
| F-11 | 规则 | 保存即试算，非法规则 400 | P0 | 已实现 |
| F-12 | 内容 | 标题 + 正文 | P0 | 已实现 |
| F-13 | 内容 | 模板变量 | P2 | 未实现 |
| F-14 | 通道 | 钉钉群机器人 ×2：各自 webhook / 加签 / 关键词 | P0 | 已实现 |
| F-14a | 通道 | 每条提醒可自选使用哪个机器人（1 / 2 / 都发） | P0 | 已实现 |
| F-15 | 通道 | 钉钉 @人 | P2 | 未实现 |
| F-16 | 通道 | 邮件：SSL(465) / STARTTLS(587)，多收件人 | P0 | 已实现 |
| F-17 | 通道 | 独立「发送测试」+ 可读失败原因 | P0 | 已实现 |
| F-18 | 通道 | 失败重试（指数退避，配置类错误不重试） | P1 | 已实现 |
| F-19 | 通道 | 通道适配器注册表（可扩展 Bark/飞书等） | P1 | 已实现 |
| F-20 | 治理 | 全局免打扰时段 | P1 | 未实现 |
| F-21 | 治理 | 错过容忍窗口 grace，超出记「错过」 | P0 | 已实现 |
| F-22 | 治理 | 钉钉限流节流（20 条/分钟） | P1 | 未实现（有队列缓冲） |
| F-23 | 记录 | 发送日志，保留最近 2000 条 | P0 | 已实现 |
| F-24 | 记录 | 规则维度统计 | P2 | 未实现 |
| F-25 | 数据 | 规则导出/导入 | P1 | 未实现 |
| F-26 | 安全 | 默认内网免密；设置 `ADMIN_TOKEN` 后启用口令 | P0 | 已实现 |
| F-27 | 集成 | `/api/health`（含时区、队列长度、版本） | P0 | 已实现 |
| F-28 | 集成 | 桌面图标、应用中心启停、卸载保留数据 | P1 | 已实现（待真机验证） |

### 3.2 核心：规则语义的精确定义

**通用约定**

- 时区**显式绑定** `Asia/Shanghai`（`zoneinfo`），不读宿主机系统时区；所有时间为东八区墙上时间。
- 时间精度到分钟；调度器每 15 秒扫描，误差 < 30 秒。
- `end` 时刻**包含**在窗口内。
- 间隔按「起点 + N×interval」步进，最后一发不超过 `end`。
- 同一规则同一分钟内只触发一次（`next_run_at` 单调推进保证幂等）。
- 规则修改后立即重算 `next_run_at`，不保留旧队列。

**F-04 每日时段内周期提醒**

```
参数：start, end, interval_minutes, days[]（空=每天）
生成：t0=start, t1=t0+interval, ... 直到 > end
```

| 例子 | 结果 |
| --- | --- |
| 09:00–18:00，间隔 60 分 | 09:00 … 18:00（共 10 次） |
| 09:00–18:00，间隔 90 分 | 09:00, 10:30, 12:00, 13:30, 15:00, 16:30, 18:00（7 次） |
| 09:30–17:00，间隔 120 分，周一/三/五 | 09:30, 11:30, 13:30, 15:30, 17:00 |
| 22:00–02:00，间隔 60 分（跨零点） | 22:00, 23:00, 00:00, 01:00, 02:00（归属起始日） |

> **跨零点的实现要点**：`next_after()` 的扫描窗口必须从**前一天**开始，否则 00:00 之后
> 会漏掉前一天窗口溢出的 01:00 / 02:00。这一点已有单测锁定。

**F-21 错过与补发**

| 场景 | 行为 |
| --- | --- |
| 正常在线 | 准时推送 |
| 到点后 ≤ grace 才重启 | 补发一次 |
| 关机 3 小时后开机 | 跳过历史点，只重算下一次，日志记「错过」 |
| 暂停期间到点 | 不触发，恢复后从恢复时刻重算 |

> **实现红线**：迟到秒数必须用 `timedelta.total_seconds()`。`.seconds` 只返回"去掉整天后的余数"，
> 关机 25 小时会算成 3600 秒，grace 判定彻底失真。已封装为 `clock.seconds_late()`，
> 并用源码扫描测试禁止 `.seconds` 与裸 `datetime.now()` 再次出现。

### 3.3 Snooze 与一次性规则的生命周期（v1.1 新增）

**Snooze（F-10）**

- 采用**方案 B**：`rules` 表增加 `snooze_until` 字段，**不改动 `next_run_at`**。
- 语义：`snooze_until = now + N 分钟`，到点后**额外**发一次，原定周期节奏不变。
- 调度查询同时命中两者：`next_run_at <= now OR snooze_until <= now`。
- 同一次 tick 内若两者都到期，只发一次（snooze 优先），并推进 `next_run_at` 防止重复。
- 发送完成即清空 `snooze_until`；同一规则再次 snooze 会覆盖前一次（不做队列叠加）。

为什么不改 `next_run_at`：直接改写会把"13:00 该响的那一次"顶到 13:15，之后整条序列全部漂移，
用户看到的下次触发时间也会变得不可解释。

**一次性规则（F-01）完成后**

- 发送完成后 `next_run_at = NULL`、`last_run_at` 有值，`enabled` 保持不变。
- 状态由后端派生为 `state = completed`（不新增数据库枚举，避免迁移）：
  ```
  completed = (type == once) && last_run_at && !next_run_at
  paused    = !enabled
  active    = 其他
  ```
- 前端对 `completed` 卡片置灰、徽章显示「已完成」，只保留「重新设置 / 删除」。
- 好处：用户一眼区分"自己暂停的"和"已经跑完失效的"，且不会因为自动 `enabled=0` 与手动暂停混淆。

### 3.4 节假日/工作日（P2 预留设计）

当前 `days` 只表达"星期几"。中国的调休会出现"周六上班、周一到周五放假"，因此约定：

- 保持 `days` 语义不变，新增**可选** `calendar` 字段（未来）：`{"calendar": "cn_workday"}`。
- 规则引擎中判断"当天是否生效"抽出为独立钩子 `is_active_day(rule, date)`；
  现在等价于"星期匹配"，将来接入节假日表只替换这一个函数，调度与存储层零改动。
- v1.2 再做：内置当年节假日表 + 手工标记"补班日"。

### 3.5 页面与交互

```
顶部栏：标题 | [通知设置] [新建提醒]
Tabs：提醒列表 | 发送日志
卡片 = 状态徽章(启用/已暂停/已完成) + 标题 + 规则人话描述
      + 下次触发 + 稍后时间 + 通道 + 上次触发
      + [试发] [稍后] [跳过本次] [暂停/启用] [编辑] [删除]     （已完成态仅剩后两项）
新建/编辑弹层：标题 / 正文(支持 Markdown+emoji，带快捷表情与「预览效果」) / 触发方式(联动字段) / 星期 / 通道(钉钉机器人1 ☐ 机器人2 ☐ 邮件 ☐) / 启用
通知设置弹层：钉钉机器人1(webhook, secret, keyword, 测试) | 钉钉机器人2(同上) | 邮件(host,port,ssl,user,pass,sender,to, 测试) | 消息样式(钉钉 Markdown / 邮件纯文本，表情·颜色·变量插入 + 实时预览) | grace
```

视觉：浅色渐变背景（`linear-gradient(160deg,#eef3ff,#f7f8fb,#eafaf3)`，`background-attachment:fixed`），
顶栏半透明毛玻璃，卡片保持白底以维持对比度；不自绘复杂主题，跟随浏览器字体栈。

### 3.6 权限与安全

- **默认免密**：局域网内可访问者均可查看/修改（决策 D3，仅内网使用的前提）。
- **可选口令**：设置环境变量 `ADMIN_TOKEN=xxx` 后：
  - `/api/*` 需带 `X-Admin-Token` 头（或 `?token=` 参数），否则 401；
  - `/api/health` 免鉴权供探活；静态页照常返回，前端首次调用时弹窗要口令并记住；
  - 启动日志会打印 `auth=token|open(内网)`，避免"以为开了口令其实没开"。
- 钉钉 secret、邮箱授权码接口回传掩码 `******`，提交掩码值不覆盖原值；密钥只落本地 SQLite。
- 建议：外网访问走 FN Connect / VPN；或反代加 Basic Auth。**不要直接把 8388 映射到公网。**

### 3.7 消息模板（v1.3 新增）

**问题**：v1.2 的钉钉消息是硬编码的 `### 标题` + 正文 + 三行元信息，用户无法加 emoji、改排版、
给关键字段上色；而一旦把同一段文字直接丢进邮件，又会满屏 `###` 和 `<font>`。

**方案：模板 + 变量，按通道各渲染一次。**

- 两套模板（`settings.template.dingtalk` / `.text`），由通道自己声明用哪套（`BaseChannel.text_format`）：
  钉钉 = `md`（Markdown 原样交给钉钉渲染），邮件 = `text`（渲染后 `to_plain()` 去掉标题井号、加粗、颜色标签）。
- 占位符：`{title}` `{content}` `{time}` `{date}` `{weekday}` `{rule}` `{reason}` `{channels}` `{next}`；
  **未知占位符原样保留**（拼错时预览里能立刻看见，而不是被静默吞掉）。
- `{content}` 是**直通**的：用户在提醒正文里写的 `**加粗**`、`- 列表`、`<font color=#e11d48>红字</font>`、
  emoji 都会原样进入钉钉消息，由客户端渲染。
- 默认模板刻意做成「信息卡」：`### 🔔 {title}` + 正文 + 三行灰色小字元信息（`#8f959e`）。
- 颜色三条硬规则（钉钉客户端限制）：六位十六进制、标签成对闭合、**不加引号**
  （`color=#FF0000` 电脑+手机均生效；`color='#FF0000'` 仅电脑端；`color=red` 不生效）。
- 换行（v1.3.1 修复）：钉钉的 Markdown 渲染器**只认空行分段**，单个 `\n` 会被吞成同一段，
  表现就是多行文字在群里挤成一行。因此钉钉格式在渲染末尾过一遍 `harden_breaks()`：
  给相邻的普通文本行之间补一个空行；**连续列表项之间不补**（`- a` / `- b` 本就各占一行），
  代码块内原样保留。邮件是纯文本，单换行天然生效，不做此处理。
- 边界：正文超 8000 字符截断并注明（钉钉单条消息约 20000 字节上限）；模板留空 = 跟随内置默认
  （与默认完全一致时也存空串，将来升级默认样式，没自定义过的用户自动跟上）。
- 安全：预览是把渲染结果塞进 `innerHTML`，因此 `md_to_html()` **先整体 `html.escape()`，再放行
  `<font color=#hex>`**，模板里写 `<script>` 只会被显示成文本。
- 交互：模板编辑器带「表情 / 颜色 / 变量」快捷插入、实时预览（防抖 260ms + 序号防乱序）、
  恢复默认；「编辑提醒」里另有「预览效果」，用当前标题/正文 + 已保存模板渲染，不必先保存。

---

## 4. 技术方案
### 4.1 架构

```
        ┌──────────────────────────────────────────────────┐
        │                    fnOS 环境                       │
        │                                                  │
 浏览器  │  ┌────────────────────────────────────────────┐  │
 ─────►  │  │ Web 层  ThreadingHTTPServer（每请求一线程）   │  │
        │  └───────────────┬────────────────────────────┘  │
        │                  │ REST /api                       │
        │  ┌───────────────▼────────────────────────────┐  │
        │  │ 业务层  规则引擎 rules.next_after()          │  │
        │  │ CRUD / 校验 / 重算 next_run_at              │  │
        │  └───────────────┬────────────────────────────┘  │
        │                  │                                │
        │  ┌───────────────▼──────────┐   ┌──────────────┐ │
        │  │ 调度线程 Tick 15s         │   │ SQLite (WAL) │ │
        │  │ 查到期 → 入队 → 推进 next │◄──┤ rules/logs/kv│ │
        │  └───────────────┬──────────┘   └──────────────┘ │
        │                  │ put(rule)                     │
        │  ┌───────────────▼────────────────────────────┐  │
        │  │ 发送队列 queue.Queue + 2 个工作线程          │  │
        │  │ 重试(5s/15s) → 逐通道落日志                 │  │
        │  └───────────────┬────────────────────────────┘  │
        │                  │                                │
        │  ┌───────────────▼────────────────────────────┐  │
        │  │ 通道适配器 BaseChannel（注册表可扩展）        │  │
        │  │  ├─ DingTalk  webhook + HMAC 加签           │  │
        │  │  └─ Email     SMTP SSL / STARTTLS           │  │
        │  └───────────────┬────────────────────────────┘  │
        └──────────────────┼───────────────────────────────┘
                           │ HTTPS
              ┌────────────┼────────────┐
              ▼                         ▼
      oapi.dingtalk.com           SMTP 服务器
```

### 4.2 关键技术决策

| 决策点 | 采纳方案 | 理由 |
| --- | --- | --- |
| 交付形态（D1） | fpk 原生应用包 | 有图标、可启停、无需镜像仓库、包体最小 |
| 语言/依赖（D2） | Python 标准库，零第三方依赖 | fnOS 的 python312 直跑，无 pip |
| Web 并发 | `ThreadingHTTPServer`（ThreadingMixIn）+ HTTP/1.1 | 慢请求（SMTP 20s）不阻塞其他请求 |
| 长耗时操作 | 调度只入队，发送走工作线程池 | tick 不被网络抖动拖慢 |
| 时间 | `zoneinfo("Asia/Shanghai")` + naive 墙上时间 | 不依赖宿主机时区，避免 UTC 机器错 8 小时 |
| 调度 | tick 轮询 + `next_run_at` 落库 | 重启不丢、可单测、无额外依赖 |
| 存储 | SQLite WAL + busy_timeout + 索引 | 并发读写不锁死，单文件备份 |
| 前端 | 原生 HTML/CSS/JS 单页 | 免构建链 |
| 鉴权（D3） | 默认免密，`ADMIN_TOKEN` 可选 | 兼顾极简与安全 |
| 通道（D4） | 钉钉 + 邮件，`BaseChannel` 注册表 | 扩展只需新增适配器 |

### 4.3 并发模型（v1.1 新增）

线程划分：

| 线程 | 数量 | 职责 | 阻塞风险 |
| --- | --- | --- | --- |
| HTTP 处理 | 每请求 1 个（daemon） | 解析、读写库、试发 | 试发会占用该请求线程 20s，但其他请求不受影响 |
| scheduler | 1 | 查到期、入队、推进 next | 只做内存与短 SQL，不发网络请求 |
| sender | 2 | 真正发送 + 重试 + 写日志 | 网络慢只影响队列深度 |

关键约束：

1. **调度线程绝不发网络请求**。SMTP 握手超时 20s、钉钉 15s，同步发送会把后续规则整体推迟。
2. 队列上限 500，满时丢本次并写日志（宁可丢一条，不可拖垮进程）。
3. 每个工作表单独 `sqlite3.connect(timeout=10)` + `PRAGMA busy_timeout=10000`，不跨线程共享连接。
4. 规则更新是单条 `UPDATE`（`rule_json` 与 `next_run_at` 同语句），不会出现半更新状态。

### 4.4 数据模型

```sql
CREATE TABLE rules(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  title        TEXT NOT NULL,
  body         TEXT NOT NULL DEFAULT '',
  rule_json    TEXT NOT NULL,
  channels     TEXT NOT NULL DEFAULT '[]',
  enabled      INTEGER NOT NULL DEFAULT 1,
  next_run_at  TEXT,          -- 正常触发点，NULL=无后续
  snooze_until TEXT,          -- 稍后提醒（独立于 next_run_at）
  last_run_at  TEXT,
  created_at   TEXT NOT NULL
);

CREATE TABLE logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, rule_id INTEGER, title TEXT,
  channel TEXT, ok INTEGER, detail TEXT
);

CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);

-- 调度热路径：每 15 秒按 (enabled, next_run_at) 扫一次
CREATE INDEX IF NOT EXISTS idx_rules_next_run ON rules(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_logs_ts  ON logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_logs_rule ON logs(rule_id, id DESC);
```

- `PRAGMA journal_mode=WAL` + `synchronous=NORMAL`：读写并发友好。
- 老库兼容：启动时 `PRAGMA table_info(rules)` 检测缺列并 `ALTER TABLE ADD COLUMN snooze_until`。
- 日志裁剪：插入后 `DELETE ... NOT IN (SELECT id ... LIMIT 2000)`，长期运行不膨胀。

**规则 JSON**

```json
{"type":"once",         "at":"2026-10-30 20:00"}
{"type":"daily",        "times":["09:30","17:30"]}
{"type":"weekly",       "days":[1,3,5], "times":["09:00"]}
{"type":"daily_window", "start":"09:00","end":"18:00","interval_minutes":60,"days":[1,2,3,4,5]}
{"type":"interval",     "start":"2026-10-23 09:00","every_minutes":90}
```

`days`：`1=周一 … 7=周日`，空/省略 = 每天。

**channels 取值与配置分块**（v1.2）

| channel 键 | 界面名 | 对应设置块 | 说明 |
| --- | --- | --- | --- |
| `dingtalk1` | 钉钉机器人1 | `settings.dingtalk1` | 一套 webhook/secret/keyword |
| `dingtalk2` | 钉钉机器人2 | `settings.dingtalk2` | 另一套，互不影响 |
| `email` | 邮件 | `settings.email` | SMTP |

- `channels` 仍是字符串数组，例如 `["dingtalk1","dingtalk2","email"]`；写入前统一 `normalize_channels()`：去重、丢未知项。
- 历史兼容：v1.1 及以前规则里存的 `"dingtalk"` 读出来即归一为 `dingtalk1`（别名表 `ALIASES`），老库零迁移。
- 历史配置兼容：设置里遗留的 `dingtalk` 块在读取时自动并入 `dingtalk1`（仅当 `dingtalk1` 未填 webhook），随后从内存配置中移除，不会回写污染。
- 日志表 `channel` 列存 channel 键，前端映射为中文名；因此「机器人1 / 机器人2」在日志里能分开看。

### 4.5 接口清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 免鉴权；返回 `time/tz/version/queue/auth` |
| GET | `/api/rules` | 列表（含 `desc`、`state`、`snooze_until`） |
| POST | `/api/rules` | 新建（校验+试算，失败 400） |
| PUT | `/api/rules/{id}` | 更新（重算 next_run_at） |
| DELETE | `/api/rules/{id}` | 删除 |
| POST | `/api/rules/{id}/toggle` | 暂停 / 启用 |
| POST | `/api/rules/{id}/skip` | 跳过本次 |
| POST | `/api/rules/{id}/snooze` | `{"minutes":15}`，1–1440 |
| POST | `/api/rules/{id}/test` | 立即试发（同步，返回最近日志） |
| GET / POST | `/api/settings` | 读写配置（密钥掩码） |
| POST | `/api/settings/test` | `{"channel":"email"\|"dingtalk1"\|"dingtalk2"}`（旧名 `dingtalk` 走别名） |
| GET | `/api/template` | 两套模板当前值 + 内置默认 + 变量表（v1.3） |
| POST | `/api/template/preview` | `{format,template,title,content,rule}` → `{title,text,html,chars}`，不入库（v1.3） |
| GET | `/api/logs` | 最近 120 条 |

错误统一 `{"error":"可读原因"}`；鉴权失败额外带 `{"need_token":true}`。

### 4.6 通道适配器

```python
class ChannelConfigError(Exception):
    """配置类错误：重试没有意义，立即失败。"""

class BaseChannel:
    name = ""          # 规则里存的键，如 "dingtalk2"
    label = ""         # 界面文案，如 "钉钉机器人2"
    settings_key = ""  # 指向设置里的配置块
    timeout = 20
    text_format = "md" # "md"=钉钉 Markdown，"text"=邮件纯文本（v1.3）
    def send(self, cfg, title, body) -> tuple[bool, str]: ...   # body 已是渲染好的文本
    def send_with_retry(self, cfg, title, body, retries=2, backoff=(5, 15)): ...

REGISTRY = {
    "dingtalk1": DingTalkChannel("dingtalk1", "钉钉机器人1", "dingtalk1"),
    "dingtalk2": DingTalkChannel("dingtalk2", "钉钉机器人2", "dingtalk2"),
    "email":     EmailChannel("email", "邮件", "email"),   # text_format = "text"
}
ALIASES = {"dingtalk": "dingtalk1"}   # 旧数据兼容

# 渲染入口（v1.3）：ctx 是模板变量字典，每个通道按自己的 text_format 渲染
def dispatch(channels, settings, ctx, retries=2): ...
```

- **同一通道多实例**：`name` 与 `settings_key` 解耦，所以两个钉钉机器人共用一个 `DingTalkChannel` 类，只是配置块不同。
- 新增 Bark / Server酱 / 飞书：实现 `send()` 后注册进 `REGISTRY`，调度与存储层零改动。
- **配置类错误不重试**：没填 webhook 也重试 2 次会让用户干等 20 秒才看到日志，体验极差。
- 网络类错误重试 2 次（5s、15s），日志会标注"已重试 2 次"。

**钉钉要点**：`sign = urlencode(Base64(HMAC-SHA256(secret, timestamp + "\n" + secret)))`；
`errcode != 0` 即失败，`310000` 多为签名/关键词问题，原样回显；单机器人限流 20 条/分钟。

**邮件要点**：465 → `SMTP_SSL`，587 → `STARTTLS`；QQ/163 必须用**授权码**（UI 已明示）。

### 4.7 目录结构

```
fnnas-reminder/
├─ app/
│  ├─ server.py       # HTTP + 调度线程
│  ├─ clock.py        # 统一时间入口（时区绑定 / seconds_late）
│  ├─ rules.py        # 规则引擎（纯函数）
│  ├─ store.py        # SQLite（索引 / 迁移 / 状态派生）
│  ├─ notify.py       # BaseChannel + 邮件/钉钉适配器
│  ├─ template.py     # 消息模板（Markdown / 纯文本 / 预览 HTML）
│  ├─ dispatcher.py   # 发送队列 + 工作线程 + 重试
│  └─ static/index.html
├─ fpk-src/           # manifest / cmd / config / ui / app / 图标
├─ scripts/           # build_fpk.sh、make_icons.py
├─ tests/             # test_rules.py、test_clock.py、test_template.py、smoke_test.py
├─ README.md
└─ 项目书.md
```

### 4.8 调度伪代码（已修正）

```python
while True:
    now = clock.now()                       # 东八区 naive
    grace = settings["grace_seconds"]
    for rule in store.due_rules(clock.fmt(now)):      # next_run_at 或 snooze_until 到期
        sent = False
        if rule.snooze_until and rule.snooze_until <= now_str:
            queue.put((rule, "snooze")); clear_snooze(rule); sent = True
        if rule.next_run_at and rule.next_run_at <= now_str:
            if not sent:
                late = clock.seconds_late(now, rule.next_run_at)   # total_seconds！
                if late <= grace: queue.put((rule, "schedule"))
                else:            log_missed(rule, late)            # 关机太久，跳过
            set_next(rule, next_after(rule, now))                  # 单调推进
    sleep(15)
```

---

## 5. fnOS 集成方案

### 5.1 为什么只做 fpk 原生

| 对比项 | fpk 原生（采纳） | fpk + Docker | 纯 Docker |
| --- | --- | --- | --- |
| 依赖 | fnOS 的 python312 | 需镜像仓库可达 | 需装 Docker |
| 包体 | ~100KB | 60MB+ | — |
| 图标/启停 | 有 | 有 | 无 |
| 上架应用中心 | 可以 | 可以 | 不能 |
| 风险 | 受运行时版本约束 | 需维护镜像 | 集成度低 |

### 5.2 打包与集成要点

- 工具 `fnpack`（Windows 下载无后缀，需改名 `fnpack.exe` 并加入 PATH）
- 命令：`./scripts/build_fpk.sh`（生成图标 → 同步代码 → `fnpack build -d fpk-src`）
- 安装：应用中心「手动安装」上传 `.fpk`；或 `appcenter-cli install-fpk fnnas.reminder.fpk`
- `manifest`：`appname=fnnas.reminder`、`version=1.3.2`、`source=thirdparty`、`platform=x86`、
  `service_port=8388`、`checkport=true`、`desktop_uidir=ui`、
  `desktop_applaunchname=fnnas.reminder.Main`、`install_dep_apps=python312`
- 生命周期：`main start` 用 PID 文件拉起，2 秒自检失败时把日志尾部写进 `TRIM_TEMP_LOGFILE` 并 `exit 1`；
  `main status` 运行中 `0`、未运行 `3`；`install_callback` 建 `TRIM_PKGVAR/data`
- 数据目录：程序 `TRIM_APPDEST`，数据 `TRIM_PKGVAR`（卸载保留）
- 图标：`ICON.PNG`(64)、`ICON_256.PNG`(256) 为包图标；桌面入口图标必须在 `app/ui/images/icon_64.png`、`icon_256.png`（`desktop_uidir=ui` 是相对**应用目录**解析的，放错位置装完不出图标）
- ARM：`manifest` 改 `platform = arm` 重新打包

---

## 6. 里程碑与验收

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| M1 骨架 | 规则引擎、存储、API、单页 UI | ✅ 12 个规则单测 |
| M2 通道 | 钉钉加签、SMTP、测试按钮、失败原因 | ✅ 待真实凭据联调 |
| M3 稳定性 | 重启重算、grace、日志、非法规则拦截 | ✅ 57 项端到端 |
| M4 体验 | 暂停、跳过、snooze、完成态、密钥掩码、可选口令、双机器人通道、渐变主题 | ✅ |
| M5 fnOS 集成 | manifest、cmd、图标、组装脚本 | ✅ 真机安装验证通过 |
| M6 真机验收 | 装包 → 建规则 → 收消息 → 重启演练 | ⏳ |

**测试资产**：`tests/test_rules.py`（12）+ `tests/test_clock.py`（7，含"禁止 `.seconds`/裸 `datetime.now()`"的源码扫描）+ `tests/test_channels.py`（10，双机器人/别名/旧配置迁移）+ `tests/test_frontend.py`（6，禁止原生弹窗/标题/渐变）+ `tests/smoke_test.py`（41）。

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 关机超 24 小时导致 grace 误判 | 开机补发一堆旧提醒 | `clock.seconds_late()` 用 total_seconds + 源码扫描测试防回归 |
| 宿主机时区为 UTC | 全部时间偏移 8 小时 | `zoneinfo` 显式绑定，启动日志打印 `tz=` 便于核对 |
| SMTP 慢导致界面卡死 | 无法操作 | ThreadingHTTPServer + 发送队列，慢请求只占自己的线程 |
| SQLite 锁冲突 | 保存规则报 database is locked | `timeout=10` + `busy_timeout` + WAL + 索引 |
| 钉钉限流 20 条/分钟 | 消息丢失 | 队列缓冲；v1.1 余下做主动节流 |
| 配置错误被重试掩盖 | 用户干等 20 秒 | `ChannelConfigError` 直接失败并即时写日志 |
| NAS 关机漏提醒 | 提醒失效 | grace + 日志标记；文档说明适用边界 |
| python312 缺失 | 启动失败 | `install_dep_apps` + 启动前 `command -v python3` 检查并弹窗 |
| 端口占用 | 启动失败 | `checkport=true` + `PORT` 环境变量 |
| 无鉴权暴露公网 | 被乱改规则 | 默认内网 + `ADMIN_TOKEN` + 反代/VPN 三种加固 |
| 同库双进程 | 重复推送 | PID 文件互斥 + 单实例约定 |

---

## 8. 决策记录

| # | 决策 | 结论 |
| --- | --- | --- |
| D1 | 交付形态 | **只做 fpk 原生应用**（评审建议的"Docker 先行"作为开发期便利可选，不进入交付物） |
| D2 | 技术栈 | **标准库 Python**，并要求落实 ThreadingHTTPServer 与 zoneinfo |
| D3 | 访问控制 | 默认**免密**（仅内网），支持 `ADMIN_TOKEN` 环境变量开启口令 —— 采纳评审的"方案 ① 变体" |
| D4 | 一期通道 | 钉钉群机器人（v1.2 起支持 **2 个**）+ 邮件，`BaseChannel` 抽象，后续零侵入扩展 |

---

## 9. 路线图

| 版本 | 内容 |
| --- | --- |
| v1.1 | 时间修复、时区绑定、并发模型、索引与事务、snooze、完成态、适配器抽象、可选口令、重试 |
| v1.2（本次） | 两个钉钉群机器人 + 每条提醒自选通道、应用名统一为「定时提醒」、渐变背景 |
| v1.3 | 免打扰时段、钉钉主动节流、规则导入导出、每月某日、模板变量、节假日日历（`calendar` 钩子） |
| v1.4 | Docker 模板并行构建、上架应用中心 |

---

## 附录 A：修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v0.9 | 2026-10-23 | 初稿 |
| v1.0 | 2026-10-23 | 依 D1–D4 定稿：fpk 原生、去掉鉴权、补实现状态 |
| v1.1 | 2026-10-23 | 按评审意见修复时间/并发/存储缺陷，补 snooze 与完成态，鉴权改为可选口令 |
| v1.2 | 2026-10-23 | 通道由 1 个钉钉机器人扩为 2 个且每条提醒可自选；应用名统一为「定时提醒」；界面改渐变背景 |
| v1.2.1 | 2026-10-23 | 修复 iframe 内嵌时原生 confirm/prompt 被拦截导致删除、稍后提醒无反应；改页内弹层 |
| v1.2.2 | 2026-10-23 | 试发只回本条规则的结果（此前串到别的规则的邮件失败）；未配置通道改为跳过并在 UI 标注 |
| v1.3 | 2026-10-23 | 新增可自定义消息模板：钉钉按 Markdown 渲染（标题/加粗/引用/列表/链接 + emoji + `<font>` 彩色文字），带表情/颜色/变量快捷插入与实时预览；邮件自动降级为纯文本 |
| v1.3.1 | 2026-10-23 | 修复钉钉消息不换行：新增 `harden_breaks()` 把单回车升级为空行分段（列表项保持紧凑） |
| v1.3.2 | 2026-10-23 | 开源首发：安全加固（`hmac.compare_digest` 口令比较、静态文件 `realpath` 收口、请求体上限 256 KB、写接口只收 `application/json`）、补 LICENSE/CI/`.gitattributes`、README 重写 |

---

## 附录 B：评审意见处置对照表

| # | 评审意见 | 处置 | 落点 |
| --- | --- | --- | --- |
| 1.1 | `timedelta.seconds` 截断导致关机 >24h 误判 | **已改代码**：新增 `clock.seconds_late()` 强制 `total_seconds()`；文档伪代码同步修正；新增源码扫描测试禁止 `.seconds` 复活 | `app/clock.py`、`app/server.py`、`tests/test_clock.py`、§3.2、§4.8 |
| 1.2 | 未显式绑定时区，宿主 UTC 会错 8 小时 | **已改代码**：`zoneinfo.ZoneInfo("Asia/Shanghai")`，对外统一 naive 墙上时间；`/api/health` 与启动日志打印 `tz` | `app/clock.py`、§4.2、§7 |
| 2.1 | `HTTPServer` 单线程阻塞、界面假死 | **已实现（原设计如此）**：使用 `ThreadingHTTPServer`；文档补明确要求与 HTTP/1.1 | `app/server.py`、§4.2 |
| 2.2 | 长耗时发送不可阻塞主请求线程 | **已改代码**：新增 `dispatcher.py` 发送队列 + 2 工作线程；调度线程只入队 | `app/dispatcher.py`、§4.3 |
| 3.1 | `database is locked` | **已改代码**：`timeout=10` + `PRAGMA busy_timeout=10000` + WAL | `app/store.py` |
| 3.2 | 缺索引 | **已改代码**：`idx_rules_next_run`、`idx_logs_ts`、`idx_logs_rule` | `app/store.py`、§4.4 |
| 3.3 | 规则更新需事务一致 | **已满足并说明**：`rule_json` 与 `next_run_at` 在**同一条 UPDATE** 内完成，天然原子 | `app/store.py`、§4.3 |
| 4.1 | snooze 未落库、两方案未定 | **已定方案 B 并实现**：新增 `snooze_until` 字段（含老库 `ALTER TABLE` 迁移），不改 `next_run_at`，调度双条件命中 | §3.3、§4.4、`app/store.py`、`app/server.py` |
| 4.2 | 一次性规则触发后的状态 | **已实现**：派生 `state=completed`，卡片置灰、只留"重新设置/删除"；不改 `enabled` 以免与暂停混淆 | §3.3、`app/store.py`、`index.html` |
| 4.3 | 节假日/调休预留 | **已写入设计**：保留 `days` 语义，预留 `calendar` 字段与 `is_active_day()` 钩子，v1.3 实现 | §3.4 |
| 5.1 | D1 建议 Docker 先行 | **部分采纳**：交付形态定为 fpk 原生（贴近应用中心体验），Docker 仅作为开发期可选手段，不产出交付物 | §8 |
| 5.2 | D2 要求多线程 + zoneinfo | **已采纳并实现** | §4.2、`app/clock.py` |
| 5.3 | D3 可选 `ADMIN_TOKEN` + 免密模式 | **已采纳**：未设 `ADMIN_TOKEN` 即免密；设置后 API 需口令，静态页可打开由前端提示输入 | §3.6、`app/server.py`、`index.html` |
| 5.4 | D4 通道抽象基类 | **已采纳**：`BaseChannel` + 注册表 + `ChannelConfigError`（配置错误不重试） | §4.6、`app/notify.py` |
