# DayKeep 设计文档

日期：2026-10-17
状态：待评审

## 1. 目标与非目标

**目标**：一个在本地运行的 AI 日记 Agent。用户和 AI 聊天，AI 在聊天过程中自主把一天里发生的事记成日记。仓库开源到 GitHub，别人 clone 后一条命令就能用。

**期次**：一期做完整可用的 Web 形态，也就是本 spec 的全部内容。桌面外壳放二期。一期前端因此不引入构建步骤，二期用 pywebview 复用同一份前端产物，页面一行不改。

**隐私说明**（会写进 README）：程序和数据都在本地，但聊天内容会发送给用户自己配置的第三方 API。

## 2. 技术栈

| 层 | 选择 | 理由 |
|---|---|---|
| 语言 / 工程 | Python 3.12 + uv | AI 生态的 wheel 已跟上；`uv run daykeep` 就是全部上手步骤 |
| Agent 内核 | Pydantic AI v2（`pydantic-ai-slim[openai]`） | 循环、工具 schema 派生、参数校验重试、流式事件都由库负责 |
| 模型接入 | `OpenAIChatModel` + `OpenAIProvider(base_url, api_key)` | 任何 OpenAI 兼容服务（DeepSeek、通义、智谱等）都能接 |
| 后端 | FastAPI + uvicorn，SSE 用 `StreamingResponse` 手写 | 只有一个流式端点，不额外引入 SSE 库 |
| 前端 | 原生 HTML/CSS/JS，没有构建步骤 | 不需要 node_modules；二期 pywebview 直接复用 |
| Markdown 渲染 | 服务端用 `markdown-it-py`（`html=False`） | 前端不引第三方 JS；LLM 输出里夹带的原始 HTML 会被转义，杜绝 XSS |
| 配置 | `tomllib` + Pydantic 模型 | 标准库读 TOML，Pydantic 做类型校验，出错时能给出明确提示；不额外引入配置库 |
| 日记 front-matter | PyYAML | |
| 测试 | pytest + anyio + httpx；Pydantic AI 的 `FunctionModel` / `TestModel` | 整条链路测试不联网、不花钱、结果确定 |
| 静态检查 | ruff | |

## 3. 模块划分

```
daykeep/
  pyproject.toml
  README.md
  src/daykeep/
    __main__.py     # 入口：读配置 → 起 uvicorn(127.0.0.1) → 打开浏览器
    config.py       # Settings；首次运行生成配置模板
    clock.py        # logical_date(now, rollover_hour) 与时间戳格式化
    models.py       # Moment / DayMeta / DayRecord（Pydantic）
    store.py        # 当天记录的原子读写；导出 markdown；列出历史日期
    window.py       # 上下文窗口：token 估算、裁剪事件、渲染窗口（纯函数）
    agent.py        # Agent 定义、instructions、7 个工具
    server.py       # FastAPI 路由与 SSE 事件协议
    web/            # index.html / app.js / style.css
  tests/
```

每个模块的职责和依赖：

- **clock**：纯函数，不依赖其他模块。
- **models**：只放数据结构，不含读写逻辑。
- **store**：依赖 models，只负责文件读写，包括列出有记录的日期和读取某一天的日记（跨天检索用）。所有写入都是先写临时文件再 `os.replace`，进程中途崩溃不会留下写了一半的文件。
- **window**：依赖 models 和 Pydantic AI 的消息类型。输入是"完整归档 + 预算"，输出是"这一轮发给模型的消息列表 + 丢弃了多少轮"，是纯函数，不碰文件也不存状态。
- **agent**：依赖 store、window、clock。工具通过 `RunContext[Deps]` 拿到当天的记录对象和逻辑日，**日期由程序注入，模型不能自己填日期**。
- **server**：串联以上所有模块，管请求级的锁与持久化。

## 4. 数据模型与存储

### 4.1 目录布局

数据根目录默认是 `~/DayKeep`，可以在配置里改。**数据不放在仓库目录里**，避免用户给项目提 PR 时把自己的日记一起提交上去。

```
~/DayKeep/
  config.toml                 # 首次运行时生成模板
  entries/2026-10-17.md       # 给人看的日记（由程序生成，Obsidian 可直接打开）
  .daykeep/days/2026-10-17.json   # 当天的唯一数据源
```

### 4.2 当天记录（`DayRecord`）是唯一数据源

```python
Mood = Literal["很好", "不错", "一般", "低落", "糟糕"]

class Moment(BaseModel):          # 一天中的一件事
    id: int                       # 当天内自增，由程序分配
    time: str | None              # "HH:MM"，用户提到时间才填
    title: str
    content: str
    mood: Mood | None = None

class DayMeta(BaseModel):
    title: str | None = None      # 这一天的标题
    mood: Mood | None = None
    tags: list[str] = []
    summary: str | None = None

class DayRecord(BaseModel):
    date: date
    meta: DayMeta
    moments: list[Moment]
    messages: list[ModelMessage]  # 当天完整对话，通过 ModelMessagesTypeAdapter 序列化
```

`entries/*.md` 在每次日记内容变化后从 `DayRecord` 重新生成：front-matter 写 date/title/mood/tags，正文是 summary 加上按时间排序的各个 moment。**这个 md 文件只用来阅读，直接修改会在下次生成时被覆盖**，README 里要写清楚这一点。

### 4.3 逻辑日

`logical_date = (now - timedelta(hours=rollover_hour)).date()`，`rollover_hour` 默认是 4。凌晨 1 点聊的内容算前一天。每次请求都会重新计算逻辑日；如果日期变了，就进入新的一天，开新会话。

## 5. 上下文管理（定版）

### 5.1 原则

- **归档和窗口分开**。归档保存当天全部原文，没有上限；窗口是每一轮实际发给模型的那一段。界面上始终显示完整对话，用户永远不会看到"前面的内容没了"。
- **一天一个会话**。这是天然的任务边界，所以不需要压缩。
- **不做 LLM 摘要**。在装得下的范围内保留原文，召回率更高，也更便宜（能命中 prompt cache）。装不下时，用不需要调用模型、结果确定的方式裁剪，再配上明确的丢弃通知。
- **instructions 不进入历史**，所以裁剪永远不会丢掉人设和记录规则（Pydantic AI 本身就是把 instructions 每轮随请求发送、不入历史）。
- **裁剪结果是归档的纯函数**，不存任何"窗口状态"。同样的归档和预算必然算出同样的窗口，不会出现存储状态与归档不一致，缓存前缀也天然稳定。

### 5.2 每一轮请求的结构

```
[instructions：人设 + 记录规则 + 今天的逻辑日期 + 最近 7 天的回顾]   ← 整天不变，可以被缓存
[丢弃通知]（只有发生过裁剪才有）                                    ← 只在裁剪事件时变化
[窗口内的历史，只追加、不修改]                                      ← 前缀稳定，可以被缓存
[本轮用户消息："[21:30] 原文"]
```

- 当前时间以 `[HH:MM] ` 前缀的形式写进用户消息，这样时间信息进入只追加的历史，不会破坏缓存。界面显示消息时会去掉这个前缀。
- 当天日记的内容**不放进 instructions**（放进去的话，每记一条日记都会让后面的缓存全部失效）。模型通过工具返回值和 `read_today_entry` 了解已经记了什么。
- 回顾块是跨天记忆的载体：每天开始时，把最近 7 天的 `DayMeta` 渲染成几行固定文本（日期 + 标题 + 心情 + 摘要；没有摘要时退化为当天的 moment 标题），放进 instructions。它整个逻辑日不变，所以不影响缓存。回顾的是**已记下的日记**，不是过去的聊天原话 —— 原话由 `search_today_chat` 检索。

### 5.3 裁剪事件

**预算由模型自己的上下文长度推出来，不由我们猜。** Pydantic AI 的 `model.context_window` 会从 genai-prices 自动取到真实值，配置里只需要在"模型不被认识"或"想主动限制发送量"时覆盖。默认：

- `budget = context_window - 32000`。32k 是预留量而不是比例：预留它给本轮回复、instructions 和一轮里新增的内容。1M 窗口的模型因此拿到约 968k 的预算，128k 的模型拿到约 96k。
- 配置里可以直接写 `budget` 覆盖上面的推导。调小它会牺牲模型对较早对话的记忆，换来更低的延迟和费用 —— 这个取舍写进 README。
- 探测不到 `context_window` 时（Pydantic AI 返回 `None`，表示未知而不是零）必须由配置显式给出；两者都缺时启动失败并说明要填哪个字段。

**判断是否超预算用真实用量，不用字符估算。** 上一轮模型返回时会带 provider 上报的 `total_tokens`，它就存在归档里那条消息上，直接读。只有 provider 没上报时才回退到字符估算（CJK 字符每个算 1 个 token，其他每 4 个算 1 个）。估算函数刻意不用真正的 tokenizer：结果确定、可测试，也不会因为 tokenizer 版本变化而漂移。

**每轮请求发出之前检查**：`上一轮的 total_tokens + 本轮消息的规模` 超过 `budget` 就触发一次裁剪：

1. 从最老的一端丢起，**一次性降到 `budget × 0.7` 以下**，切点落在用户发言边界上（不会切出"有工具调用、没有工具结果"的历史）。丢的轮数还必须对齐到固定块（20 轮）的整数倍 —— 因为拿到的始终是完整归档，若按"刚好塞得下的起点"丢，归档每长一轮起点就往后挪一轮，窗口开头每轮都在变，prompt cache 永远命中不了；对齐到固定块之后，一次裁剪后的若干轮内起点钉死不动，前缀才稳定。
2. 至少保留最近 30 轮。只有"最近 30 轮本身就超预算"（用户连续贴了极长的内容）时才会少于 30 轮。
3. 在窗口开头生成丢弃通知：`已省略本日更早的 N 轮对话。当前日记的内容如下：<当前日记渲染出来的文本>。需要更早的原话时用 search_today_chat 查找。`

不存窗口状态（早期草稿里的 `WindowState` 已删除）：`start` 和丢弃通知都是当场从归档算出来的。两次裁剪之间窗口只在末尾追加、前缀不变，缓存一直命中。

这套做法和 Pydantic AI 文档里 "Compact when the context window fills" 的模式一致：按窗口占用触发、按整轮保留。区别只是把那一处的摘要换成了确定性裁剪 + 丢弃通知。

**为什么这样对用户体验好**：预算贴着模型真实上限给，正常使用（哪怕一天聊几百轮）根本不会触发，只有极端长的会话才降级。即使降级了，模型也知道自己省了什么、手上有最新的日记全文，还能检索回原话，不会无声无息地"失忆"。界面上会显示一行浅色提示"更早的对话已不在 AI 的直接记忆中，需要时它会自己查找"。

### 5.4 单条消息上限

一条消息的规模不能超过 `budget × 0.5`。超过时返回 413，界面提示"这条太长了，分几次发"。阈值跟着预算走，所以在 1M 窗口的模型上几乎不可能触发 —— 它只在"一条消息就要占掉半个窗口"这种真会出问题的情况下才拦。

## 6. Agent 与工具

### 6.1 instructions 的要点

- 人设：温和的倾听者，像朋友一样聊天，不做审问式提问；在合适的时候自然地追问细节和感受。
- 记录规则：听到值得记的事就调用 `upsert_moment`。同一件事的后续补充，用已有的 id 去更新，不要新建；用户说"这个别记"时调用 `delete_moment`；对这一天的整体感受有了结论时调用 `set_day_meta`。
- 记录的时候不用打断聊天，也不用每次都告诉用户"我记下了"（界面会显示记录提示）。
- 遇到用户的指代（"那个"、"刚才说的"）而你又不确定指什么时，先用 `search_today_chat` 搜；搜不到就直接问，不要编。
- 聊到过去几天的事时，先看 instructions 里的回顾块；需要细节用 `read_entry` 读那一天的日记，需要找具体话题用 `search_entries`。同样地，找不到就问，不要编。
- 今天的逻辑日期是 {date}（星期 X）。

### 6.2 工具

| 工具 | 参数 | 返回 |
|---|---|---|
| `upsert_moment` | `id: int \| None`, `time`, `title`, `content`, `mood` | `"已记录 #3 <title>"`（返回很短，避免历史膨胀） |
| `delete_moment` | `id: int` | `"已删除 #3"`；id 不存在时抛 `ModelRetry`，并列出现有的 id |
| `set_day_meta` | `title`, `mood`, `tags`, `summary`（都可选，只更新传了的字段） | `"已更新"` |
| `read_today_entry` | 无 | 当天日记的完整渲染文本，每个 moment 都带 id |
| `search_today_chat` | `query: str` | 在**今天完整归档**里做大小写不敏感的子串匹配；最多返回 5 个命中，每个命中返回所在的**整轮**用户发言和 AI 回复（带时间）；没有命中时返回明确的"未找到" |
| `read_entry` | `date: date` | 某一天日记的完整文本；那天没有日记时返回"那天没有记录" |
| `search_entries` | `query: str`, `days_back: int = 30` | 在最近 N 天的**日记正文**里做子串匹配，最多返回 5 个命中，每个命中带日期和上下文片段 |

跨天检索查的是**日记正文**而不是原始聊天：过去的聊天已经被蒸馏成日记，日记就是它的检索面。今天的聊天还没蒸馏完，所以 `search_today_chat` 查原始记录。

参数 schema 全部由函数签名和 Pydantic 模型自动生成。参数校验失败由 Pydantic AI 自动让模型重试。

工具修改 `DayRecord` 的同时立即写盘并重新生成 md，然后通过 SSE 推一个 `entry` 事件，界面右侧的日记预览会实时刷新。

## 7. 请求流程与 SSE 协议

### 7.1 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | 前端页面 |
| GET | `/api/today` | 当前逻辑日、完整对话记录（给界面展示用）、日记 HTML、配置状态 |
| POST | `/api/chat` | `{text}` → SSE 流 |
| GET | `/api/days` | 有日记的日期列表 |
| GET | `/api/days/{date}` | 某一天的日记 HTML（只读） |

服务只监听 `127.0.0.1`。

### 7.2 一轮对话的处理

1. 加全局 `asyncio.Lock`（单用户，同一时刻只处理一轮；前端在等待回复时禁用发送按钮）。
2. 计算逻辑日，加载或新建 `DayRecord`。
3. 执行裁剪检查（5.3 节），渲染窗口。
4. `agent.run_stream(prompt, message_history=window, deps=..., event_stream_handler=...)`，把事件逐个翻译成 SSE 发给前端。
5. 运行成功后，把 `new_messages()` 追加到归档，原子写盘。

SSE 事件：

| event | data | 前端行为 |
|---|---|---|
| `day` | `{date}` | 如果和当前显示的日期不同，就清空界面重新加载 |
| `delta` | `{text}` | 把文字追加到正在生成的回复里 |
| `tool` | `{label}` | 在聊天流里显示一行浅色提示，例如"✎ 已记录：腾讯二面" |
| `entry` | `{html}` | 刷新右侧日记预览 |
| `trimmed` | `{dropped_turns}` | 显示一行浅色提示，说明更早的对话已移出 AI 的直接记忆 |
| `done` | `{}` | 恢复发送按钮 |
| `error` | `{message, retryable}` | 在界面上显示错误；用户刚输入的文字放回输入框 |

### 7.3 错误处理

| 情况 | 处理 |
|---|---|
| 没有配置 API key | 启动时在终端打印配置文件路径；界面顶部显示横幅，说明要填哪些字段；`/api/chat` 直接返回 `error`，不调用模型 |
| 网络错误 / 429 / 5xx | 发 `error(retryable=true)`；**这一轮的消息不写入归档**，用户点重试会重新发送同样的内容 |
| 服务商返回上下文超长 | 说明 token 估算偏小了：用 `budget × 0.5` 作为目标强制裁剪一次，自动重试一次；还失败就报错 |
| 工具参数不合法 | Pydantic AI 自动让模型重试，界面无感 |
| 生成到一半用户关闭页面 | 这一轮的消息不入档；已经执行的工具对日记的修改会保留。这一点接受：下一轮模型可以用 `read_today_entry` 看到最新状态 |
| 写盘失败 | 原子写保证旧文件完好；发 `error(retryable=false)` |

## 8. 界面

一个页面，两栏：

- **左栏：聊天**。显示当天完整的对话记录，AI 的回复流式输出；记录提示和裁剪提示以浅色小字插在消息之间。输入框按 Enter 发送，Shift+Enter 换行。
- **右栏：今天的日记**。实时显示渲染后的日记。
- **顶部栏**：当前逻辑日期，以及一个"往日"下拉菜单。选中某天后，右栏切换成那天的只读日记，并提供"回到今天"按钮。

二期用 pywebview 套壳时，这个页面一行都不用改。

## 9. 配置

`~/DayKeep/config.toml`，首次运行时生成模板：

```toml
[model]
base_url = "https://api.deepseek.com"   # 任意 OpenAI 兼容地址
api_key = ""                            # 必填
name = "deepseek-chat"

# 下面两项通常不用填：上下文长度由 Pydantic AI 自动探测（model.context_window）。
# 只有模型不被认识，或者想主动限制每轮发送量时才覆盖。
# context_window = 1000000
# budget = 900000

[app]
port = 8765
rollover_hour = 4
```

也可以用环境变量 `DAYKEEP_API_KEY` 覆盖 `api_key`。README 会提醒用户不要把这个文件提交到任何地方。

## 10. 测试策略

全部测试离线运行，结果确定：

- **clock**：凌晨 3:59 和 4:00 两个时刻，分别落在哪一天。
- **store**：在临时目录里读写 `DayRecord`，结果一致；md 导出和快照逐字节比对；原子写失败时旧文件保持完好。
- **window**（核心，最重要）：
  - 窗口的第一条一定是用户发言；不会出现"有工具调用、没有工具结果"的情况。
  - 裁剪后的估计 token ≤ `budget × 0.7`，并且至少保留 30 轮。
  - 判超预算时优先采用归档里上一轮上报的 `total_tokens`；provider 没上报时才回退到字符估算。
  - **缓存稳定性**：在两次裁剪事件之间连续渲染多轮，前一轮发出的窗口一定是后一轮窗口的前缀。
  - `notice` 只在裁剪事件时变化。
- **agent**（用 `FunctionModel` 写好脚本，模拟模型的每一步动作）：
  - 模型调用 `upsert_moment` 后，`DayRecord` 和 md 都正确更新；用已有 id 调用是更新而不是新建。
  - `delete_moment` 传入不存在的 id，会触发重试。
  - **召回测试**：构造一段超过预算的对话，让第 3 轮被裁剪出窗口；脚本化的模型调用 `search_today_chat("腾讯")`，断言返回结果里有第 3 轮的原文。
  - **跨天回顾**：回顾块包含最近 7 天的日期、标题、心情、摘要；缺少摘要的那天退化成 moment 标题；同一天内连续多轮渲染出的回顾块逐字节相同（缓存稳定）。
  - **跨天检索**：`search_entries("实习")` 能命中若干天前的日记正文并返回那一天的日期；`read_entry` 读一个没有记录的日期时返回明确的"没有记录"。
- **server**：用 httpx 的 ASGI 客户端配合 `TestModel` 发起一轮对话，断言 SSE 事件流以 `day` 开头、以 `done` 结尾，中间至少有一个 `delta`（`tool`/`entry` 可能和 `delta` 交错出现，所以不对它们的相对顺序做断言）；模拟模型抛出网络错误时，断言收到 `error`，并且归档没有被写入。
- **界面**：实现完成后用浏览器自动化走一遍主流程：发送消息 → 流式回复 → 右栏日记更新 → 刷新页面后对话记录还在 → 切换到往日查看。

## 11. 分期

- **一期（本 spec 的范围）**：以上全部内容。
- **二期**：pywebview 桌面窗口（`daykeep --app`）；界面里的配置页面。
- **暂不考虑**：本地模型、embedding 检索、摘要压缩。触发重新评估的条件是：日志显示 `search_today_chat` 经常找不到，同时用户频繁问到更早的内容。即使到那时，第一步也是改进检索，而不是加摘要。
