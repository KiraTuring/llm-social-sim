# LLM 社会模拟引擎 - Agent 指南

## 入口

- **唯一入口**: `run.py`（main.py 和 run_tavern.py 已删除）
- 运行: `python3 run.py --scene tavern --ticks 10 --mode interactive`
- 列出场景: `python3 run.py --list-scenes`
- 保存状态: `python3 run.py --scene tavern --ticks 10 --mode auto --save saves/run.json`
- 继续运行: `python3 run.py --load saves/run.json --ticks 5 --mode auto`（`--scene` 会和 `--load` 冲突）

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
    render_config: dict = {}
```

可选:
```python
    gm_llm_prompt: str = ""          # LLM GM 的 system prompt
    connections: list[tuple[str, str]] = []  # 地点连通边（双向），空=全连通
```
Agent 配置 `<list[dict]>` 每个元素必须包含: `name`, `role`, `personality`, `goal`, `location`, `relationships`（启动时自动校验，缺字段立即报错）。

场景类自动被 `--list-scenes` 识别（排除 `_` 开头和 `base.py`、`utils.py`）

### 地点连通性

未定义 `connections` 时所有地点互相可达（向前兼容）。有定义时 `MoveAction` 只能移动到相邻地点：

```python
connections = [
    ("主厅", "吧台"),
    ("主厅", "角落"),
    ("吧台", "后厨"),
]
```

### Action 注册

场景通过 `setup()` 方法注册 actions:

```python
def setup(self, registry: ActionRegistry):
    registry.register(MyCustomAction())
    # common actions 在场景外 core/actions/common.py
```

### 自定义 Action 的 tool schema

每个 Action 注册时自动生成独立 tool schema。可覆盖 `get_tool_schema()` 自定义参数：

```python
class MyAction(ActionSpec):
    name = "myaction"
    description = "自定义行动"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "content": {"type": "string"},
                        "internal_monologue": {"type": "string", "description": "内心独白"},
                    },
                    "required": ["target"],
                },
            },
        }
```

`get_tool_schema()` 不接收 `locations` 参数（schema 不编译 enum）。参数合法性由 `validate_params()` 运行时校验。

### 参数校验（validate_params）

每个 Action 可以覆盖 `validate_params(params, context)` 方法对参数做运行时校验。`context` 包含 `agent_name`、`agent_location`、`agent_names`、`locations`、`agents_by_location`、`hearable_agents`、`adjacent_locations`。

返回 `None`=合法，`str`=错误信息：

```python
class MyAction(ActionSpec):
    def validate_params(self, params, context):
        target = params.get("target", "")
        locations = context.get("locations", [])
        if target and target not in locations:
            return f"'{target}' 不是有效位置，可选: {', '.join(locations)}"
        return None
```

内置 Action 的校验规则：

| Action | 规则 |
|--------|------|
| `speak` | target 不能是自己；必须在 `agent_names` 且在 `hearable_agents` 中 |
| `whisper` | target 不能为空/自己；必须在 `agent_names` 且与说话者在同一位置 |
| `move` | target 不能是当前位置；必须在 `locations` 中且在 `adjacent_locations` 内 |

校验失败时 LLM 会收到错误提示并重试（最多 2 次），超限 fallback 到 `observe`。

### 规则注册

场景通过 `setup_rules()` 方法注册规则引擎的事件处理：

```python
def setup_rules(self, engine: RuleEngine):
    @engine.on("speech")
    def _on_speech(msg, world):
        # 处理对话情绪影响
        ...
```

规则不注册则对应事件静默忽略。通用规则引擎不包含任何场景数据。

### 渲染配置

场景通过 `render_config` 定义展示信息：

```python
class MyScene(Scene):
    render_config = {
        "location_icons": {"前线": "⚔️", "营地": "🏕️", "哨塔": "🏰"},
    }
```

`ConsoleRenderer` 自动读取 `location_icons`，不存在的位置用 `📍` 兜底。

## MessageBus 关键细节

**Agents 必须先注册才能接收消息:**

```python
# 在 Scene.init_world() 中自动调用，但手动创建时需注意:
world.message_bus.register_agent(agent_name)
```

广播消息只到达已注册的 agents，未注册的 agents 收不到 "all" 消息。

### Message 结构

`target` 字段区分直接目标和旁观者：

```python
@dataclass
class Message:
    sender: str          # 发送者
    recipients: list[str]  # 所有收到的人（目标 + 旁观者）
    target: str | None   # 直接目标（如说话对象），None=广播
    content: str
    msg_type: str
    tick: int
```

- `speak -> 艾莉娅`：recipients=`[艾莉娅, 旁观者]`，target=`艾莉娅`
- `whisper -> 雷恩`：recipients=`[雷恩]`，target=`雷恩`
- 广播（`speak` 无 target）：recipients=`["all"]`，target=`None`

## LLM 集成

### DeepSeek 配置

- **模型格式**: `deepseek/deepseek-chat`（litellm 格式 `provider/model`）
- **API**: litellm，使用 `api_base` 参数（不是 `base_url`）
- 模型名从 config 读取（`config.yaml` 的 `provider` + `model`），不再硬编码

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

仍不调用工具则 fallback 到 `observe`，控制台提示 `[LLM] {name} 重试耗尽，使用 fallback`。

### 参数校验重试

LLM 调用了不合法工具或参数时（如 target 不存在、位置不对），`validate_params()` 返回错误信息，自动追加到 messages 重试（最多 2 次）。参数校验和「无 tool call」共享同一个重试计数器，超限统一 fallback。

## 配置加载

环境变量展开语法: `${VAR_NAME}`

```yaml
api_key: "${DEEPSEEK_API_KEY}"
```

### 日志配置

```yaml
logging:
  file: "logs/simulation.log"
  level: "INFO"  # DEBUG, INFO, WARNING, ERROR
```

### Agent 配置

```yaml
agent:
  memory_short_limit: 10
  memory_compress_threshold: 30
  content_max_length: 200  # 记忆和消息的统一截断长度
  max_energy: 100          # Agent 初始精力值
  inbox_limit: 5           # 每次 perceive 看到的收件箱消息数
  relation_display_limit: 3  # 印象中显示的关系事件数
```

## Agent 流程

每个 tick 按 `action_order` 依次执行每个 agent:

1. **`perceive()`** → 读取 inbox → 构建上下文（环境 → 状态 → 记忆 → 收件箱） → inbox 消息写入记忆 → **清空 inbox**
2. **`think()`** → 调用 LLM 生成 Action
3. **`act()`** → 通过 ActionRegistry 查找并执行，**自动将行动摘要写入短期记忆**

**同 tick 行动可见性**：排在前面的 agent 说话/行动后，排在后面的 agent 能在本 tick 内看到（通过 perceive 读到的 inbox 消息）；反之，排在前面的 agent 要等到下一 tick 才知道后面的人做了什么。

### 上下文顺序

perceive() 构建的 LLM prompt 顺序：

```
【当前环境】    ← 位置、同位置的人、可见范围
【你的状态】    ← 情绪、精力
【你最近记得的事】 ← 记忆（短期 + 摘要 + 关系）
【你收到的消息】  ← 当前 inbox 内容（处理后清空）
```

### inbox 生命周期

```
perceive() → 读取并清空 inbox
act()      → 发送消息到所有 inbox（包括自己）
下一个 tick → perceive() 读取跨 tick 存活的消息
```

**不跨 tick**：每条消息在 `perceive()` 中恰好被看到一次，然后清空。最后一个人的消息存活到下一 tick。

### 手动控制（ManualAgent）

通过文件 `manual_actions.json` 控制指定 agent 的行动，不走 LLM：

```bash
python3 run.py --scene tavern --ticks 5 --mode auto --manual 老巴克
```

可指定自定义 JSON 路径：
```bash
python3 run.py --scene tavern --ticks 5 --mode auto --manual 老巴克 --manual-file my_actions.json
```

```json
{
  "老巴克": {
    "1": {"action_type": "speak", "target": "艾莉娅", "content": "欢迎光临"},
    "3": {"action_type": "move", "target": "主厅", "content": "走向主厅"}
  }
}
```

未在 JSON 中配置的 tick → 自动 `observe`，不阻塞。

## 记忆系统

### 写入时机

- **`agent.act()`**: 每次执行 action 后写入 `[{action_type}] 你: {content[:content_max_length]} (目标: {target})`
- **`ObserveAction.execute()`**: 通过 `action.result` 写入 `[observed] 你在{位置} | 看到: {人名}({角色})在{位置} - 情绪:{情绪}，...`
- **`perceive()`**: 将收到的 inbox 消息写入记忆，格式 `[{msg_type}] 你: {content[:content_max_length]}`（`你` 替换了 sender/target 中的自身名字）

所有截断长度由 `content_max_length`（config，默认 200）统一控制。

### 读取方式

`perceive()` → `memory.get_context()` → 依次输出：
- `【你的过去】`：压缩摘要（如有）
- `【你最近记得的事】`：短期记忆（全部，压缩后 `_short_term` 被物理截断）

### 存储结构

| 仓库 | 用途 | 限制 |
|------|------|------|
| `_short_term` | 最近事件列表 | `short_limit`（config，默认 10） |
| `_summary` | LLM 压缩摘要 | 超出 `compress_threshold`（默认 30）时触发 |
| `_relations` | 对其他 agent 的印象 | 无限制 |

### 记忆压缩

`_short_term` 达到 `compress_threshold`（config，默认 30）条时，`perceive()` 末尾自动触发 `compress()`：

1. 取前 `threshold - short_limit` 条旧事件（默认 20 条）
2. 调用 LLM 将其与现有 `_summary` 合并压缩为 3-5 句新摘要（第二人称视角）
3. `_short_term` 截断为最后 `short_limit` 条（默认 10 条）
4. LLM 调用失败时静默跳过，不丢失数据

## 可见性系统（Visibility）

观察范围大于交互范围。场景通过 `visibility` 定义每个位置能看到的其他位置：

```python
class TavernScene(Scene):
    visibility = {
        "主厅": ["吧台", "角落", "壁炉旁", "后厨"],
        "吧台": ["主厅", "壁炉旁"],
        "角落": ["主厅", "壁炉旁"],
        "壁炉旁": ["主厅", "吧台"],
        "后厨": ["主厅"],
    }
```

- 未定义 `visibility` = 只能看到同位置的 agent（向前兼容）
- 空列表 `[]` = 同位置也看不到其他人（如暗室）
- `ObserveAction` 自动从所有可见位置收集 agent 信息并存入记忆（**正向**：我能看到的位置）
- `SpeakAction` 对特定目标说话时，能看到说话者的旁观者也会收到消息（**反向**：谁能看到我）

## 环境状态系统（Environment）

每个场景可以定义 `initial_environment`，为每个位置设置结构化环境指标。环境状态是格式化的 key-value 对，值统一用 `str`（`"97%"`、`"+0.02°"`、`"正常"`），由 GM 事件驱动更新，供 ObserveAction 读取：

```python
class SpaceshipScene(Scene):
    initial_environment = {
        "驾驶舱": {"航向偏差": "+0.02°", "引擎温度": "正常", "重力": "稳定"},
        "引擎室": {"引擎震动": "轻微", "冷却效率": "100%", "燃料": "87%"},
        "生活舱": {"温度": "22°C", "氧气": "正常"},
        "医疗舱": {"医疗物资": "充足"},
    }
```

### 数据流

```
Scene.initial_environment → WorldState.environment (init_world)
                    ↓
GM 计划事件 (3-tuple)       ──→ update_environment() ──→ environment 变更
GM LLM modify_environment   ──→ update_environment() ──→ environment 变更
                    ↓
Agent observe ──→ ObserveAction.execute() 读当前+可见位置环境
                    ↓
action.result = {"observed": "...环境数据..."}
                    ↓
Agent.act() → memory.add("[observed] ...")
```

### 三种变更路径

- **计划事件（确定性）**：`gm_events` 格式升级为 `(tick, text, changes?)`，`changes = {"位置": {"指标": "新值"}}`。2-tuple 无变更，向后兼容。
- **LLM GM（动态）**：GM 的 ActionRegistry 注册了 `modify_environment` 工具，LLM 可在 ReAct 循环中调用 `generate_event`（叙事）+ `modify_environment(location, key, value)`（改状态），支持并行调用。
- **随机事件**：保持纯文本，暂不携带变更。

### ObserveAction 输出

ObserveAction 读取当前位置 + visibility 中所有可见位置的环境数据：

```
你在驾驶舱 | 环境: 驾驶舱(航向偏差 +0.02°, 引擎温度 正常), 引擎室(引擎震动 轻微, 冷却效率 100%) | 看到: 芬恩(导航员)在驾驶舱 - 情绪:冷静
```

- 环境段只在有数据的位置出现
- 所有位置都无 environment → 整个「环境」段消失（tavern 向后兼容）
- 结果通过 `action.result` 自动写入记忆

### 关键方法

- `WorldState.update_environment(location, key, value)` — 更新指标，location 不合法时返回错误信息
- `WorldState.get_environment_summary(location)` — 返回 `"key value, key value"` 或 `""`
- `ModifyEnvironmentAction` — GM 工具，`validate_params()` 校验 location 有效性
- `_check_scheduled()` — 支持 3-tuple `(tick, text, changes)`，触发时自动应用 changes

## 规则引擎

事件驱动模式，监听 `message.msg_type`:

```python
@engine.on("speech")
def _on_insult(msg, world):
    if contains_insult(msg.content):
        world.agents[msg.target].modify_trust(msg.sender, -2)
```

规则必须在场景的 `setup_rules()` 中注册，核心引擎不包含任何场景数据。可用 msg_type: `speech`, `whisper`, `system_event`, `action`, `trade_offer`...

## GM Agent（Game Master）

GM 负责向世界注入外部事件，分三级触发（互不阻塞，各自独立掷骰）：

1. **计划事件**（`gm_events`）：特定 tick 触发
2. **静态随机事件**（`gm_random_events`）：每 tick 以 `random_event_chance` 概率触发
3. **LLM 动态事件**（需 `use_llm: true`）：每 tick 以 `llm_event_chance` 概率触发

### LLM 动态事件

GM 拥有自己的 `ActionRegistry`，注册 `core/actions/gm_actions.py` 中的 GM 专用 Action。当前支持:

| Action | 用途 | 未来扩展 |
|--------|------|---------|
| `generate_event` | 生成外部事件描述 | — |
| — | — | `add_agent`, `add_location`, `add_item` |
| — | — | `modify_weather`, `set_time` |
| — | — | `npc_speak`, `npc_act` |

GM 使用 `llm_client.call_multi()`（走 tool_call 模式）生成事件，支持一次响应多个工具。

### ReAct 循环

`_generate_llm_event()` 运行 `MAX_TURNS=3` 的 ReAct 循环：

```
call_multi() → dispatch 所有 action → 结果喂回 LLM → 继续或停止
```

停止条件：
- LLM 返回纯文本（`allow_no_tool=True`，视为合法停止信号）
- dispatch 无有效结果
- 达到 `MAX_TURNS` 上限

### 场景配置

```python
class MyScene(Scene):
    gm_llm_prompt = "你是这个世界的 GM，请生成..."
```

- `gm_llm_prompt` 为空字符串 = 使用空 system prompt（仅规则 + 工具描述）
- GM 的 system prompt 自动追加通用规则块 + 可用工具列表（`_build_gm_prompt()`）

### GM 上下文构建

`_build_world_context()` 提供中等粒度上下文：

- 当前 tick
- 各位置的角色分布（含情绪/精力）
- 最近 5 条事件（`world.event_log`）
- 最近 8 条对话（从 `MessageBus.get_recent(10)` 过滤 `speech`/`whisper`）

计划/随机事件在 LLM 调用前已写入 `event_log`，GM 可看到它们避免生成冲突内容。

### 校验上下文

GM 的 `validation_context` 包含 `locations` 和 `agent_names`，便于未来 Action 做参数运行时校验。

### Dispatch 机制

`_dispatch(action)` 按 `action.action_type` 查表分发：

```python
handler = {
    "generate_event": self._handle_event,
    # "add_agent": self._handle_add_agent,
}.get(action.action_type)
```

新增 Action 只需注册 + `_dispatch` 加一行映射。

## 测试

测试文件: `test/*.py`

运行需要 `.env` 文件中的 `DEEPSEEK_API_KEY`

| 测试文件 | 内容 |
|---------|------|
| `test_model.py` | LLM 基础调用和并发测试 |
| `test_agent.py` | Agent 基本流程 |
| `test_gm.py` | GM 事件注入 |
| `test_retry.py` | LLM 重试机制（无 tool call、不合法工具名、不合法参数） |
| `test_bugs.py` | Bug 修复验证（model 配置、compress 空返回、visibility 安全、message_bus 字段） |

## 日志系统

### 日志配置

日志文件: `logs/simulation.log`（每次运行覆盖）

配置: `config.yaml` 中的 `logging` 段:

```yaml
logging:
  file: "logs/simulation.log"
  level: "INFO"  # DEBUG, INFO, WARNING, ERROR
```

### 日志记录内容

- **LLM 调用**（INFO + DEBUG）:
  - System Prompt（DEBUG）
  - User Messages（DEBUG）
  - Tool Schema / Text Guide（DEBUG）
  - Raw Response（DEBUG）
  - Parsed Action（INFO）

- **模拟流程**（INFO）:
  - Tick 开始/结束
  - Agent 执行的行动
  - 消息流（DEBUG）

### 日志级别说明

- **DEBUG**: 完整的 LLM 交互细节（提示词、回复、schema）
- **INFO**: 关键事件（tick 边界、action 执行、解析结果）
- **WARNING**: 非致命问题
- **ERROR**: 致命错误

### 日志格式

```
[时间戳] [级别] 消息内容
```