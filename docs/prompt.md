# Prompt 格式与 LLM 集成

## LLM 集成

### DeepSeek 配置

- **模型格式**: `deepseek/deepseek-chat`（litellm 格式 `provider/model`）
- **API**: litellm，使用 `api_base` 参数（不是 `base_url`）
- 模型名从 config 读取（`config.yaml` 的 `provider` + `model`），不再硬编码
- **`extra_params`**: `llm:` 段可配置高级参数（如 `thinking`、`reasoning_effort`），以 YAML dict 合并到每次 `litellm.acompletion()` 调用。分层覆盖：`model`/`messages`/`tools`/`api_key`/`api_base`/`drop_params` 由代码强制（不可覆盖），`temperature` 默认 0.7 但可用 `extra_params.temperature` 覆盖，其余字段按 provider 由 `drop_params` 过滤

### 双模式 Action 解析

| 模式 | LLM 支持 | 用途 |
|---|---|---|
| `tool_call` | DeepSeek GPT-4 Claude | 稳定，推荐 |
| `text_parse` | 所有模型 | 本地 Gemma 备用 |

`text_parse` 模式要求 LLM 输出格式: `[ACTION]...[/ACTION] [CONTENT]...[/CONTENT] [THOUGHT]...[/THOUGHT]`

### 无 tool call 自动重试

LLM 返回纯文本未调用工具时，自动追加提示重试（最多 2 次）：

```
user: 请选择一个可用的工具来行动，不要只输出文字。
```

仍不调用工具则返回空行动（None，本次不执行任何行动），控制台提示 `[LLM] {name} 重试耗尽，本次无行动`。

### 参数校验重试

LLM 调用了不合法工具或参数时（如 target 不存在、位置不对），`validate_params()` 返回错误信息，自动追加到 messages 重试（最多 2 次）。参数校验和「无 tool call」共享同一个重试计数器，超限统一 fallback。

**单工具限制**：Agent 路径（`call()` → `call_multi(limit_tools=1)`）一次只保留第一个 tool call，LLM 返回多个工具时多余的**显式丢弃**并记 WARNING——不参与校验、不进入重试，chat 历史里也不会残留未执行工具的记录。GM 的 `call_multi()` 不设限制，可一次响应并执行多个工具。

## Prompt 格式

Agent 和 GM 各自支持两种 prompt 格式，通过 config 中的 `prompt_format` 切换（`"text"`=无状态扁平文本，`"chat"`=多轮持久对话）。

### text 模式（默认）

所有上下文扁平坦入单条 `user` 消息，每次 think 无状态：

```
system + [user: 环境 + 状态 + 记忆上下文(短期+摘要) + 收件箱 + 上一轮行动]
```

Agent 之间的差异只体现在 context 文本内容上。GM 每 tick 重新构建消息列表。

### chat 模式（Agent）

每轮追加到 Agent 持久化的 `_chat_history`，LLM 收到标准多轮对话：

```
system + [
  user: "【你的过去】{压缩摘要}"     ← 动态注入，不在 _chat_history 中
  user(tick1): 环境 + 状态 + 收件箱
  assistant(tool_calls): [{函数}]
  tool(tool_call_id): {执行结果}
  user(tick2): 环境 + 状态 + 收件箱
  ...
]
```

chat 模式下 `perceive()` 生成的上下文差异：

| | text 模式 | chat 模式 |
|---|---|---|
| 记忆上下文 | 注入 `memory.get_context()`（短期+摘要） | 不注入（替换为 chat_history） |
| 上一轮行动 | 注入 `_last_action` 文本片段 | 不注入（assistant 消息已包含） |

**消息结构** — `_build_chat_entries()` 根据 `action.raw_tool_calls` 决定：

- **有 `raw_tool_calls`**（tool_call 模式）→ `assistant(tool_calls)` + `tool(result)` 消息对
- **有 `raw_content`**（text_parse 模式）→ `assistant(LLM 原始文本)`
- **都没有**（ManualAgent 或 action 失败）→ `assistant("[action_type]")` 文本标签

Tool 消息内容由共享函数 `format_tool_result()` 统一生成（`core/action.py`）：

```python
def format_tool_result(action_type, result, max_length=200):
    if not result:
        return f"'{action_type}' 已执行"
    return " | ".join(str(v)[:max_length] for v in result.values())
```

**生命周期**：

```
think() → 传 _chat_history 副本给 LLM → LLM 返回 action → 设 _pending_user_msg
act()   → 执行 → _build_chat_entries() → 提交到 _chat_history
          (清空 _pending_user_msg)
perceive() → 压缩触发时调用 _truncate_chat_history() 按 tick 对齐记忆
```

**retry 不污染历史**：`think()` 传 `list(_chat_history)` 副本给 LLM，重试消息追加在副本上，不影响持久 `_chat_history`。

**save/load**：`Agent.to_dict()` 序列化 `prompt_format` 和 `chat_history`，存档恢复时还原。

### chat 模式（GM）

`gm.prompt_format: "chat"` 启用。GM 维护跨 tick 的 `_gm_history`，每轮 ReAct 的结果持久化：

```
tick1 _gm_history: [user(t1), assistant(narrate), tool(结果)]
tick2 messages:     [user(t1), assistant(narrate), tool(结果), user(t2)]
      ReAct 后:     [..., assistant(narrate), tool(结果)]
```

细节：
- 延续提示 `"如需继续使用工具..."` 在提交前过滤
- `any_actions` 标志：GM 不调用工具时不写入历史
- 截断时 `while` 循环确保首条为 `user`（避免孤立 `tool` 消息）
