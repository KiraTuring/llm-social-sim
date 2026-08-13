# World 状态与消息系统

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
- 空列表 `[]` = 只能看到同位置的 agent（自身位置始终可见，看不到其他位置）
- `ObserveAction` 从当前位置收集环境细节 + 所有可见位置的 agent 信息并存入记忆
  - 环境信息仅限当前位置（不再包含可见位置的环境）
  - `_last_observed_result` 字符串比对去重，结果相同时返回"没有新的发现"
- `SpeakAction` 对特定目标说话时，能看到说话者的旁观者也会收到消息（**反向**：谁能看到我）
- `MoveAction` 通知发送到出发位置和到达位置的 hearable agents 并集（而非全局广播）。消息内容固定为"从X移动到了Y"，若提供 `content`（移动时的行为表现，别人能看到）则追加在后面

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

## 角色框架（Character）

`core/character.py` 定义角色基类，统一 Agent 与 NPC 的位置/状态接口：

```
Character                     # 基类：name / location / states / role / personality / goal
├── Agent(Character)          # 自主行动者：memory/relationships/perceive/think/act
└── NPC(Character)            # GM 控制的轻量角色：无记忆、无自主行动、不进入 action_order
```

- `world.agents: dict[str, Agent]` 存自主 Agent；`world.npcs: dict[str, NPC]` 存 NPC
- `world.characters`（property）为只读统一视图（agents + npcs 合并）
- 位置索引 `_agents_by_location` 同时索引两者；`get_characters_in_location()` 返回全部，`get_agents_in_location()` 只返回 Agent
- `get_hearable_agents(target)` / `get_visible_locations` / `build_validation_context` 对 Agent 和 NPC 一视同仁——`agent_names` 与 `agents_by_location` 均纳入 NPC，因此 speak/whisper/narrate/modify_char_state 无需区分角色类型
- `move_character(name, location)` 统一移动 Agent 与 NPC（`npc_move` GM 工具直接复用，无 `move_npc` 单独方法）
- `add_npc()` / `remove_npc()` 成对：增删均同时维护 `npcs`、`npc_names`、位置索引；存档加载后 `npc_names` 校正为实际 npcs 实体（删除的静态 NPC 不会被 scene 基线重新播种）
- 存档 `npcs` 字段序列化全部 NPC（静态 + 动态），加载时经 `world.add_npc()` 恢复并合并进 `npc_names`
