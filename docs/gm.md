# GM Agent（Game Master）

GM 负责向世界注入外部事件，分三级触发（互不阻塞，各自独立掷骰）：

1. **计划事件**（`gm_events`）：特定 tick 触发
2. **静态随机事件**（`gm_random_events`）：每 tick 以 `random_event_chance` 概率触发
3. **LLM 动态事件**（需 `use_llm: true`）：每 tick 以 `llm_event_chance` 概率触发

## LLM 动态事件

GM 拥有自己的 `ActionRegistry`，由场景的 `setup_gm()` 方法全量白名单定义（基类默认只注册 `narrate`）。可用工具池：

| Action | 文件 | 用途 |
|--------|------|------|
| `narrate` | `core/actions/gm_tools.py` | GM 旁白：世界叙事或事件公告，支持广播/位置定向/私信 |
| `modify_environment` | `core/actions/gm_tools.py` | 修改位置环境指标，`value="delete"` 删除非预定义指标 |
| `modify_char_state` | `core/actions/gm_tools.py` | 修改角色非主观状态（精力、体力、伤势） |
| `npc_speak` | `core/actions/gm_npc.py` | 控制 NPC 说话，消息流对 Agent 透明（sender=NPC名） |
| `npc_add` | `core/actions/gm_npc.py` | 动态创建新 NPC（name/location/role/personality/goal），可在运行中添加角色 |
| `npc_move` | `core/actions/gm_npc.py` | 移动 NPC 到任意有效位置（GM 全能，不受连通性限制），用于走动或事件后离场 |
| `npc_remove` | `core/actions/gm_npc.py` | 移除 NPC（叙事上表现为"xx离开了"），静默执行，离开播报由 GM 用 `narrate` 描述 |

场景按需注册（murder 只有 3 人故不注册 `npc_add`/`modify_environment`；spaceship 无 NPC 故不注册 `npc_speak`；tavern 注册全部 7 工具，支持 GM 添加临时角色并让其说话/回应/走动/移除离场）。

GM 使用 `llm_client.call_multi()`（走 tool_call 模式）生成事件，支持一次响应多个工具。

## ReAct 循环

`_generate_llm_event()` 运行 `MAX_TURNS=3` 的 ReAct 循环：

```
call_multi() → 逐工具校验并执行 → 结果喂回 LLM → 继续或停止
```

- `call_multi` **逐工具校验→执行**（非批量），且每次执行后 `_exec` 原地刷新 `validation_context`（`npc_names`/`npc_locations` 等）——因此同一响应内可链式调用有依赖的工具（如 `npc_add` 后紧跟 `npc_speak`/`npc_move`，后者校验能看到刚添加的 NPC）
- `validation_context` 每 turn 重建，跨 turn 的工具副作用同样对后续校验可见

停止条件：
- LLM 返回纯文本（`allow_no_tool=True`，视为合法停止信号）
- dispatch 无有效结果
- 达到 `MAX_TURNS` 上限

## 场景配置

```python
class MyScene(Scene):
    gm_llm_prompt = "你是这个世界的 GM，请生成..."
```

- `gm_llm_prompt` 为空字符串 = 使用空 system prompt（仅规则 + 工具描述）
- GM 的 system prompt 自动追加通用规则块 + 可用工具列表（`_build_gm_prompt()`）

## GM 上下文构建

`_build_world_context()` 提供中等粒度上下文：

- 当前 tick
- 各位置的角色分布（含情绪/精力）
- 最近 15 条消息（从 `MessageBus.get_recent(message_limit)` 获取，含所有 msg_type）

计划/随机事件在 LLM 调用前已写入 `event_log`，GM 可看到它们避免生成冲突内容。

## 校验上下文

GM 的 `validation_context` 通过 `world.build_validation_context("GM")` 构建，包含 `agent_name`、`agent_names`、`locations`、`npc_names`、`npc_locations` 和 `interactable_keys`，便于 GM Action 做参数校验（如 `npc_speak` 验证 `npc_name` 合法性、`npc_move` 验证目标位置与"已在原位置"）。

## Dispatch 机制

GM Action 统一通过 `ActionSpec.execute()` 执行，由 `_exec` 回调统一切入并记录到 `event_log`：

```python
def _exec(action):
    spec = self.registry.get(action.action_type)
    if not spec:
        return f"未知工具: {action.action_type}"
    _, result = spec.execute("GM", action.params, world)
    from core.action import format_tool_result
    summary = format_tool_result(action.action_type, result)
    if self.logger:
        self.logger.info(f"GM 工具: {action.action_type} → {summary}")
    return summary
```

`world.add_event()` 已移入各 GM Action 的 `execute()` 内部，`_exec` 不再管理 event_log。
新增 Action 只需注册到 registry，自动被 `_exec` 处理，无需额外映射。

## GM LLM 触发条件

`GMAgent.check_and_inject()` 中 LLM 事件触发的条件：

- 上一 tick 有 `interact` 消息 → **强制触发**
- 上一 tick 有 `speech` 消息且 target 是 NPC 名 → **强制触发**
- 以上都没有 → 以 `llm_event_chance` 概率随机触发
