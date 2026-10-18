# DayKeep

一个在本地运行的 AI 日记 Agent。你和 AI 聊天，它把一天里值得记的事写进当天的日记。

## 隐私

程序和日记都在这台机器上（默认 `~/DayKeep`），日记是纯 markdown，你可以随时打开、备份、删掉。
**但聊天内容会发送给你自己配置的第三方 API** —— 这是它的工作方式，请按自己的接受程度选择服务商。

## 快速开始

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv run daykeep
```

首次运行会在 `~/DayKeep/config.toml` 生成配置模板。填好 `api_key` 后重启即可。

## 配置

```toml
[model]
base_url = "https://api.deepseek.com"   # 任意 OpenAI 兼容服务
api_key = ""                            # 必填
name = "deepseek-chat"
# context_window = 1000000              # 一般不用填
# budget = 900000                       # 一般不用填

[app]
port = 8765
rollover_hour = 4
```

- 上下文长度由 Pydantic AI 自动探测，不用手填。只有在模型不被认识、或你想主动限制每轮发送量时才覆盖 `context_window` / `budget`。
- **调小 `budget` 的取舍**：AI 会更早忘掉当天较早的对话（原文不会丢，它会自己用 `search_today_chat` 查回来），换来更低的延迟和费用。默认值贴着模型上限给，正常使用不会触发。
- `rollover_hour` 默认 4：凌晨 4 点前记的都算前一天，熬夜写日记不会被切成两天。
- 也可以用环境变量 `DAYKEEP_API_KEY` 覆盖 `api_key`。
- **不要把 `config.toml` 提交到任何仓库。**

## 数据在哪

```
~/DayKeep/
  config.toml
  entries/2026-10-17.md              # 给人看的日记
  .daykeep/days/2026-10-17.json      # 程序用的数据（对话原文 + 结构化日记）
```

`entries/*.md` 每次记完会重新生成，**改它会在下次生成时被覆盖** —— 想改内容请直接跟 AI 说。
它可以直接用 Obsidian 打开。

## 怎么用

打开页面后直接聊今天发生了什么。AI 判断某件事值得记，就会写进右侧的日记；同一件事的补充会更新原来那条，不会重复。
顶部"往日"可以回顾以前的日记。左栏始终显示当天完整的对话，即使某天聊了非常多，也不会从界面上消失。

## 路线图

- [ ] 桌面应用（pywebview 套壳，复用同一份前端）
- [ ] 界面里的配置页
