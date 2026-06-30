# LLM 社会模拟引擎 - Agent 指南

## 入口

- **唯一入口**: `run.py`（main.py 和 run_tavern.py 已删除）
- 运行: `python3 run.py --scene tavern --ticks 10 --mode interactive`
- 列出场景: `python3 run.py --list-scenes`

## 场景系统

### 创建新场景

文件位置: `scenarios/<name>.py`

类命名: `<Title>Scene`（首字母大写，如 `TavernScene`，`BattlefieldScene`）

必须实现:
```python
class MyScene(Scene):
    name: str
    locations: list[str]
    agents: list[dict]
    gm_events: list[tuple[int, str]]
    gm_random_events: list[str]
```

场景类自动被 `--list-scenes` 识别（排除 `_` 开头和 `base.py`）

### Action 注册

场景通过 `setup()` 方法注册 actions:

```python
def setup(self, registry: ActionRegistry):
    registry.register(MyCustomAction())
    # common actions 在场景外 core/actions/common.py
```

## MessageBus 关键细节

**Agents 必须先注册才能接收消息:**

```python
# 在 Scene.init_world() 中自动调用，但手动创建时需注意:
world.message_bus.register_agent(agent_name)
```

广播消息只到达已注册的 agents，未注册的 agents 收不到 "all" 消息。

## LLM 集成

### DeepSeek 配置

- **模型格式**: `deepseek/deepseek-chat`（不是 `deepseek-chat`）
- **API**: litellm，使用 `api_base` 参数（不是 `base_url`）

### 双模式 Action 解析

| 模式 | LLM 支持 | 用途 |
|---|---|---|
| `tool_call` | DeepSeek GPT-4 Claude | 稳定，推荐 |
| `text_parse` | 所有模型 | 本地 Gemma 备用 |

`text_parse` 模式要求 LLM 输出格式: `[ACTION]...[/ACTION] [CONTENT]...[/CONTENT] [THOUGHT]...[/THOUGHT]`

## 配置加载

环境变量展开语法: `${VAR_NAME}`

```yaml
api_key: "${DEEPSEEK_API_KEY}"
```

## Agent 流程

每个 tick: `perceive()` → `think()` → `act()`

- `perceive()`: 拼接 inbox + 环境 + 记忆
- `think()`: 调用 LLM 生成 Action
- `act()`: 通过 ActionRegistry 查找并执行

## 规则引擎

事件驱动模式，监听 `message.msg_type`:

```python
@on_event("speech")
def _on_insult(msg, world):
    if contains_insult(msg.content):
        world.agents[msg.target].modify_trust(msg.sender, -2)
```

可用 msg_type: `speech`, `whisper`, `system_event`, `action`, `trade_offer`...

## 测试

测试文件: `test/*.py`

运行需要 `.env` 文件中的 `DEEPSEEK_API_KEY`