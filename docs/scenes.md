# 场景系统

## 创建新场景

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
    npcs: list[dict] = []                # 静态 NPC 详细配置（name/location/role/personality/goal/states）
```
Agent 配置 `<list[dict]>` 每个元素必须包含: `name`, `role`, `personality`, `goal`, `location`, `relationships`（启动时自动校验，缺字段立即报错）。

`npcs` 与 `npc_names`：静态 NPC 由 `npcs` 列表定义（`init_world()` 自动创建轻量 NPC 实体），名字同时出现在 `npc_names` 中。GM 可在运行中通过 `npc_add` 工具动态添加新 NPC。

场景类自动被 `--list-scenes` 识别（排除 `_` 开头和 `base.py`、`utils.py`）

**测试专用场景 `scenarios/_test.py`**：`_TestScene`（2 agent + 1 静态 NPC + 全量 7 个 GM 工具），
仅测试用，`_` 前缀使其不被 `--list-scenes` 收录，但 `load_scene("_test")` 可解析（存档往返测试用）。
测试不应耦合到生产场景（tavern/murder/spaceship），通用机制测试统一用 `_TestScene`。

## 地点连通性

未定义 `connections` 时所有地点互相可达（向前兼容）。有定义时 `MoveAction` 只能移动到相邻地点：

```python
connections = [
    ("主厅", "吧台"),
    ("主厅", "角落"),
    ("吧台", "后厨"),
]
```

## Action 注册

场景通过 `setup()` 方法注册 actions:

```python
def setup(self, registry: ActionRegistry):
    registry.register(MyCustomAction())
    # common actions 在场景外 actions/common.py
```

## 自定义 Action 的 tool schema

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

## 参数校验（validate_params）

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

## GM Action 注册

GM 工具的注册方式与角色 Action 相同，由场景的 `setup_gm()` 方法定义。**格式与 Agent 的 `setup()` 一致——全量白名单**：要什么就注册什么，不要则不注册：

```python
def setup_gm(self, registry: ActionRegistry):
    from actions.gm_tools import NarrateAction, ModifyEnvironmentAction, ModifyCharStateAction
    from actions.gm_npc import NpcSpeakAction, AddNpcAction, NpcMoveAction, RemoveNpcAction
    for action_cls in [NarrateAction, ModifyEnvironmentAction, ModifyCharStateAction, NpcSpeakAction, AddNpcAction, NpcMoveAction, RemoveNpcAction]:
        registry.register(action_cls())
```

基类 `Scene.setup_gm()` **不注册任何工具**（core 不 import `actions/`）。场景覆盖 `setup_gm()` 时全量声明自己需要的工具——例如 murder 场景明确只有 3 个角色、无其他人物，就**不注册 `npc_add`**，也不注册 `modify_environment`（prompt 禁止添加新证据）。自定义 GM 工具同样在此注册：

```python
class MyScene(Scene):
    def setup_gm(self, registry):
        for action_cls in [NarrateAction, ModifyEnvironmentAction, NpcSpeakAction, MyCustomGMAction()]:
            registry.register(action_cls())
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

## 通讯 Action（`actions/communication.py`）

场景相关的远程通讯行动，目前包含 `radio`：

| Action | 说明 |
|--------|------|
| `radio` | 通过无线电与任意位置的队友通话，受环境干扰值（`{干扰, 故障}`）阻断 |

`radio` 的消息流：

```
发送方 ──→ 目标（tag="radio"，全内容）
       ├→ 发送方旁观者（tag="action"："对着无线电说了几句话"）
       └→ 接收方旁观者（tag="action"，sender=接收者："身上的无线电中传来一段通话声"）
```

旁观者在两个位置都能看到"有人在用无线电"，但都不知道内容、不知道通话对象。

## 贸易 Action（`actions/trade.py`）

`trade` 让同一位置的 Agent（含 NPC）转移金钱/物品，打通「行动 → 经济 → 关系」回路：

| Action | 说明 |
|--------|------|
| `trade` | 与同一位置的角色交易：`give_money`/`give_items`（付出）+ `take_money`/`take_items`（获得）。take 必须伴随 give（有来有往），纯 give（支付/送礼）允许 |

### 状态约定

- 钱包统一放在角色的 `states["inventory"]` 下（键名见 `core.world.INVENTORY_KEY`），内部资源名由场景自定义；trade 沿用 `金钱`=整数、`物品`={名称: 数量}
- 整个钱包应**加入 `private_states`**（他人 observe 不可见）且**不要加入 `writable_states`**（LLM 不能通过 `state_update` 自改钱物，只能通过 `trade` 转移）
- 校验期支付能力只检查行动者自己的钱包（`build_validation_context` 的 `inventory` 字段，只含自己的，不泄露他人）

### 消息流

```
行动者 ──→ 对手方（tag="trade"，含金额与物品明细）
       └→ 旁观者（tag="action"："与 X 进行了一笔交易（物品名）"——只列物品，不列金额）
```

交易完成后可在 `setup_rules()` 里监听 `trade` 事件（如 tavern 的信任规则：对手方对发起方 trust +1）。

校验失败时 LLM 会收到错误提示并重试（最多 2 次），超限返回空行动（本次不执行任何行动）。

## 规则注册

场景通过 `setup_rules()` 方法注册规则引擎的事件处理：

```python
def setup_rules(self, engine: RuleEngine):
    @engine.on("speech")
    def _on_speech(msg, world):
        # 处理对话情绪影响
        ...
```

规则不注册则对应事件静默忽略。通用规则引擎不包含任何场景数据。

## 渲染配置

场景通过 `render_config` 定义展示信息：

```python
class MyScene(Scene):
    render_config = {
        "location_icons": {"前线": "⚔️", "营地": "🏕️", "哨塔": "🏰"},
    }
```

`ConsoleRenderer` 自动读取 `location_icons`，不存在的位置用 `📍` 兜底。

## WorldState 配置方法

`init_world()` 和存档加载共用 `WorldState.apply_scene_config(scene)` 把场景级配置复制到世界状态：

```python
# 内部自动处理的字段:
# - 直接复制: locations, connections, interactable_keys, npc_names
# - 派生: _adjacency(从connections), visibility+_reverse_visibility, environment(从initial_environment), _protected_env_keys
```
