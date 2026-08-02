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
    gm_events: list[tuple[int, str]] | list[tuple[int, str, dict]] = []
    gm_random_events: list[str] = []
    render_config: dict = {}
```

可选字段:
```python
    gm_llm_prompt: str = ""              # LLM GM 的 system prompt
    world_description: str = ""          # 世界描述，注入 Agent system prompt
    connections: list[tuple[str, str]] = []  # 地点连通边（双向），空=全连通
    visibility: dict[str, list[str]] | None = None  # 可见性定义
    initial_environment: dict[str, dict[str, str]] = {}  # 每个位置的环境指标
    interactable_keys: dict[str, list[str]] = {}  # 可调节的指标
    instruction: str = ""                # 附加在 Agent system prompt 末尾的指引
    states: dict = {}                    # 所有角色默认的状态
    writable_states: list = []           # LLM 可修改的状态 key
    private_states: list = []            # 对其他角色隐藏的状态 key
    npc_names: list[str] = []            # GM 控制的 NPC 角色名
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
                    },
                    "required": ["target"],
                },
            },
        }

注意：`internal_monologue` 和 `state_update` 参数由 `ActionRegistry.get_tool_schemas()` **集中注入**，自定义 Action 不需要在 `get_tool_schema()` 中写。GM 用的 Action 不会注入这两个参数（`ActionRegistry(include_agent_params=False)`）。

`get_tool_schema()` 不接收 `locations` 参数（schema 不编译 enum）。参数合法性由 `validate_params()` 运行时校验。

### 参数校验（validate_params）

每个 Action 可以覆盖 `validate_params(params, context)` 方法对参数做运行时校验。`context` 包含 `agent_name`、`agent_location`、`agent_names`、`locations`、`agents_by_location`、`hearable_agents`、`adjacent_locations`。对于 GM 工具（无 `agent_location`），额外包含 `npc_names`。

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

### GM Action 注册

GM 工具的注册方式与角色 Action 相同，由场景的 `setup_gm()` 方法定义：

```python
def setup_gm(self, registry: ActionRegistry):
    registry.register(NarrateAction())           # GM 旁白
    registry.register(ModifyEnvironmentAction()) # 修改环境指标
    registry.register(ModifyCharStateAction())   # 修改角色状态
    registry.register(NpcSpeakAction())          # 控制 NPC 说话
```

基类 Scene 默认注册以上 4 个工具。场景可覆盖以添加自定义 GM 工具：

```python
class MyScene(Scene):
    def setup_gm(self, registry):
        super().setup_gm(registry)  # 保留默认工具
        registry.register(MyCustomGMAction())
```

GM 使用的 `ActionRegistry` 使用 `include_agent_params=False`（不含 `internal_monologue` 和 `state_update`）。

内置 Action 的校验规则：

| Action | 规则 |
|--------|------|
| `speak` | target 不能是自己；必须在 `agent_names` 且在 `hearable_agents` 中 |
| `whisper` | target 不能为空/自己；必须在 `agent_names` 且与说话者在同一位置 |
| `move` | target 不能是当前位置；必须在 `locations` 中且在 `adjacent_locations` 内 |
| `radio` | target 不能为空/自己；必须在 `agent_names` 中（无位置限制） |
| `think` | 无参数校验。必填 `internal_monologue`，结果写入记忆为 `[thought]` |
| `interact` | 可选 `modifications`，只有 `interactable_keys` 中的 key 可调 |

### 通讯 Action（`core/actions/communication.py`）

场景相关的远程通讯行动，目前包含 `radio`：

| Action | 说明 |
|--------|------|
| `radio` | 通过无线电与任意位置的队友通话，受环境干扰值（`{干扰, 故障}`）阻断 |

`radio` 的消息流：

```
发送方 ──→ 目标（msg_type="radio"，全内容）
       ├→ 发送方旁观者（msg_type="action"："对着无线电说了几句话"）
       └→ 接收方旁观者（msg_type="action"，sender=接收者："身上的无线电中传来一段通话声"）
```

旁观者在两个位置都能看到"有人在用无线电"，但都不知道内容、不知道通话对象。

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

### WorldState 配置方法

`init_world()` 和存档加载共用 `WorldState.apply_scene_config(scene)` 把场景级配置复制到世界状态：

```python
# 内部自动处理的字段:
# - 直接复制: locations, connections, interactable_keys, npc_names
# - 派生: _adjacency(从connections), visibility+_reverse_visibility, environment(从initial_environment), _protected_env_keys
```

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

### 消息可见性

- `SpeakAction` 发送给 `get_hearable_agents(speaker)`（同位置 + 能看见该位置的人）
- `MoveAction` 发送给出发位置和到达位置的 hearable agents 并集
- `RadioAction` 发送消息给目标（`msg_type="radio"`）+ 旁观者通知（`msg_type="action"`）

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
  prompt_format: "text"       # "text"(flat text) | "chat"(multi-turn chat history)
  memory_short_limit: 10
  memory_compress_threshold: 30
  content_max_length: 200  # 记忆和消息的统一截断长度
  inbox_limit: 5           # 每次 perceive 看到的收件箱消息数
```

### GM 配置

```yaml
gm:
  prompt_format: "text"              # "text"(fresh each tick) | "chat"(persistent multi-turn)
  chat_history_max_messages: 40      # chat 模式下 GM 历史消息上限
  use_llm: true
  random_event_chance: 0.2
  llm_event_chance: 0.5
  message_limit: 15                  # 世界上下文中显示的消息数
```

## Agent 流程

每个 tick 按 `action_order` 依次执行每个 agent:

1. **`perceive()`** → 读取 inbox → 构建上下文（环境 → 状态 → 记忆 → 收件箱） → inbox 消息写入记忆 → **清空 inbox**
2. **`think()`** → 调用 LLM 生成 Action
3. **`act()`** → 通过 ActionRegistry 查找并执行，**自动将行动摘要写入短期记忆**

**同 tick 行动可见性**：排在前面的 agent 说话/行动后，排在后面的 agent 能在本 tick 内看到（通过 perceive 读到的 inbox 消息）；反之，排在前面的 agent 要等到下一 tick 才知道后面的人做了什么。每条消息在 `perceive()` 中恰好被看到一次然后清空，最后一个人的消息存活到下一 tick。

### 上下文顺序

perceive() 构建的 LLM prompt 顺序：

```
【当前环境】    ← 位置、同位置的人、可见范围
【你的状态】    ← 情绪、精力
【你最近记得的事】 ← 记忆（短期 + 摘要）
【你收到的新信息】 ← 当前 inbox 内容（处理后清空）
```

### Agent 创建

`run.py` 通过 `Agent.from_config(scene, cfg, config)` 创建 agent。存档加载时传 `saved=agent_data` 恢复运行时状态：

```python
# 新建 (from_config 内部根据 scene+cfg 计算 states, 新建空记忆)
agent = Agent.from_config(scene, cfg, config)

# 存档恢复 (saved 覆盖运行时字段, scene 提供 world_description/instruction 等)
agent = Agent.from_config(scene, cfg, config, saved=agent_data)
```

保存时所有 agent 字段由 `serialize_agent()` 序列化，包括 `states`、`writable_states`、`private_states`、`last_observed_result`。

### inbox 生命周期

```
perceive() → 读取并清空 inbox
act()      → 发送消息到所有 inbox（包括自己）
下一个 tick → perceive() 读取跨 tick 存活的消息
```

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

**save/load**：`serialize_agent()` 序列化 `prompt_format` 和 `chat_history`，存档恢复时还原。

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
- **`ThinkAction.execute()`**: 通过 `action.result` 写入 `[thought] {内心独白}`
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
- `ObserveAction` 从当前位置收集环境细节 + 所有可见位置的 agent 信息并存入记忆
  - 环境信息仅限当前位置（不再包含可见位置的环境）
  - `_last_observed_result` 字符串比对去重，结果相同时返回"没有新的发现"
- `SpeakAction` 对特定目标说话时，能看到说话者的旁观者也会收到消息（**反向**：谁能看到我）
- `MoveAction` 通知发送到出发位置和到达位置的 hearable agents 并集（而非全局广播）

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
- **LLM GM（动态）**：通过 `scene.setup_gm()` 注册的 GM 工具（`narrate`、`modify_environment`、`modify_char_state`、`npc_speak`），LLM 可在 ReAct 循环中并行调用。
- **随机事件**：保持纯文本，暂不携带变更。

### ObserveAction 输出

ObserveAction 读取当前位置的环境数据 + 所有可见位置的 agent 信息：

```
你在驾驶舱 | 环境: 驾驶舱(航向偏差 +0.02°) | 看到: 芬恩(导航员)在驾驶舱 - 情绪:冷静
```

- 环境段**仅限当前位置**（可见位置的环境不再包含）
- 所有位置都无 environment → 整个「环境」段消失
- 结果通过 `action.result` 自动写入记忆
- 同位置重复 observe 返回 `"你又观察了一会儿，没有什么新的发现"`（去重基于 `_last_observed_result` 字符串比对）

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

GM 拥有自己的 `ActionRegistry`，由场景的 `setup_gm()` 方法定义。基类默认注册以下工具：

| Action | 文件 | 用途 |
|--------|------|------|
| `narrate` | `core/actions/gm_tools.py` | GM 旁白：世界叙事或事件公告，支持广播/位置定向/私信 |
| `modify_environment` | `core/actions/gm_tools.py` | 修改位置环境指标，`value="delete"` 删除非预定义指标 |
| `modify_char_state` | `core/actions/gm_tools.py` | 修改角色非主观状态（精力、体力、伤势） |
| `npc_speak` | `core/actions/gm_npc.py` | 控制 NPC 说话，消息流对 Agent 透明（sender=NPC名） |

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
- 最近 15 条消息（从 `MessageBus.get_recent(message_limit)` 获取，含所有 msg_type）

计划/随机事件在 LLM 调用前已写入 `event_log`，GM 可看到它们避免生成冲突内容。

### 校验上下文

GM 的 `validation_context` 通过 `world.build_validation_context("GM")` 构建，包含 `agent_name`、`agent_names`、`locations`、`npc_names` 和 `interactable_keys`，便于 GM Action 做参数校验（如 `npc_speak` 验证 `npc_name` 合法性）。

### Dispatch 机制

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

### GM LLM 触发条件

`GMAgent.check_and_inject()` 中 LLM 事件触发的条件：

- 上一 tick 有 `interact` 消息 → **强制触发**
- 上一 tick 有 `speech` 消息且 target 是 NPC 名 → **强制触发**
- 以上都没有 → 以 `llm_event_chance` 概率随机触发

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
| `test_chat_format.py` | chat 模式消息结构、截断、retry 隔离、text 模式回归 |

## 调试工具

### `scripts/print_prompt.py`

打印指定场景中 Agent 的完整 system prompt，方便查看 LLM 实际收到的上下文，无需运行完整模拟：

```bash
python3 scripts/print_prompt.py spaceship           # 打印所有角色
python3 scripts/print_prompt.py tavern --agent 艾莉娅  # 只打印指定角色
```

不需要 API key，毫秒级输出。`instruction` 字段的内容会出现在 prompt 末尾「输出要求」下方。

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