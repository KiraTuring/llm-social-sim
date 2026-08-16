# Agent 流程

每个 tick 按 `action_order` 依次执行每个 agent:

1. **`perceive()`** → 读取 inbox → 构建上下文（环境 → 状态 → 记忆 → 收件箱） → inbox 消息写入记忆 → **清空 inbox**
2. **`think()`** → 调用 LLM 生成 Action
3. **`act()`** → 通过 ActionRegistry 查找并执行，**自动将行动摘要写入短期记忆**

**同 tick 行动可见性**：排在前面的 agent 说话/行动后，排在后面的 agent 能在本 tick 内看到（通过 perceive 读到的 inbox 消息）；反之，排在前面的 agent 要等到下一 tick 才知道后面的人做了什么。每条消息在 `perceive()` 中恰好被看到一次然后清空，最后一个人的消息存活到下一 tick。

## 模拟引擎（`core/engine.py`）

CLI 与 TUI 共用 `SimulationEngine`，tick 主循环只维护一份，避免两套逻辑漂移：

```python
engine = SimulationEngine(world, gm, llm, rule_engine, logger, config)
```

两种用法：

- **完整 tick**（CLI/测试）：`actions = await engine.run_tick(tick)`，返回 `{agent_name: action}`。
- **Agent 级步进**（TUI）：`begin_tick(tick)` → 循环 `step_agent()`（返回 `AgentStep`，全部执行完返回 `None`）→ `end_tick()`。

引擎负责：设置 `world.tick`、GM 注入、每个 Agent 的 perceive→think→act、逐条消息触发 `rule_engine.trigger()`、行动/消息日志、行动顺序轮换。不感知任何 UI——渲染、延时、等待按键由调用方负责（CLI 在 `run_simulation`，TUI 在 `_simulation_loop`）。

辅助属性：`engine.next_agent` / `engine.pending_agents` 供 UI 显示进度（如 `3/5`）。

服务（logger/LLM/rule_engine）统一由 `app.factory.setup_services` 创建后注入，TUI 不再自行构建。注意 `Scene.get_gm_config()` 会深拷贝 `gm_events`——GM 触发时会移除已触发事件，不拷贝会让同一进程里的多个引擎互相干扰。

## 上下文顺序

perceive() 构建的 LLM prompt 顺序：

```
【当前环境】    ← 位置、同位置的人、可见范围
【你的状态】    ← 情绪、精力
【你最近记得的事】 ← 记忆（短期 + 摘要）
【你收到的新信息】 ← 当前 inbox 内容（处理后清空）
```

## Agent 创建

`app/factory.py` 通过 `app.factory.create_agent(scene, cfg, config, registry=registry)` 创建 agent（registry 由场景 `setup()` 装配后**构造注入**，运行期不再改变）。存档加载时传 `saved=agent_data` 恢复运行时状态：

```python
# 新建 (from_config 内部根据 scene+cfg 计算 states, 新建空记忆)
agent = app.factory.create_agent(scene, cfg, config, registry=registry)

# 存档恢复 (saved 覆盖运行时字段, scene 提供 world_description/instruction 等)
agent = app.factory.create_agent(scene, cfg, config, registry=registry, saved=agent_data)
```

保存时所有 agent 字段由 `Agent.to_dict()` 序列化（GM 由 `GMAgent.to_dict()`，存档版本迁移入口为 `save_load._migrate()`），包括 `states`、`writable_states`、`private_states`、`last_observed_result`。

## inbox 生命周期

```
perceive() → 读取并清空 inbox
act()      → 发送消息到所有 inbox（包括自己）
下一个 tick → perceive() 读取跨 tick 存活的消息
```

## 手动控制（ManualAgent）

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

未在 JSON 中配置的 tick → 返回空行动（None，本次无行动），不阻塞。

细节：
- 支持 `"*"` 通配 tick：未单独配置的 tick 重复执行该行动（优先级低于具体 tick）
- 文件缺失或 JSON 格式错误 → **启动时直接报错退出**（不静默）
- 行动非法（未知 `action_type`、`target` 不可达、必填字段缺失等）→ 记 WARNING 并返回空行动，与 LLM 路径校验行为一致
- 示例文件：`manual_actions.example.json`（已加入仓库，可 `--manual-file` 直接使用）

## 记忆系统

### 写入时机

- **`agent.act()`**: 每次执行 action 后写入 `[{action_type}] 你: {content[:content_max_length]} (目标: {target})`
- **`ObserveAction.execute()`**: 通过 `action.result` 写入 `[observed] 你在{位置} | 看到: {人名}({角色})在{位置} - 情绪:{情绪}，...`
- **`ThinkAction.execute()`**: 通过 `action.result` 写入 `[thought] {内心独白}`
- **`perceive()`**: 将收到的 inbox 消息写入记忆，格式 `[{tag}] 你: {content[:content_max_length]}`（`你` 替换了 sender/target 中的自身名字）

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
