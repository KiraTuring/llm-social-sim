# LLM 社会模拟引擎 - Agent 指南

## 项目概览

LLM 驱动的多 Agent 社会模拟引擎：每个 Agent 有性格/目标/记忆，通过 LLM 生成行动（说话、移动、观察、思考），GM（Game Master）注入外部事件驱动世界演进。

```
core/           纯领域核心（端口、领域模型、引擎、通用算法）
app/            应用装配层（config / factory，唯一允许 import memory+llm+scenarios+actions）
actions/        内置动作实现库（common / communication / gm_tools / gm_npc ...）
scenarios/      场景定义与场景发现/加载（tavern / murder / spaceship / _test ...）
scripts/        工具脚本（print_prompt.py 等）
test/           测试
```

## 入口

- **唯一入口**: `run.py`（main.py 和 run_tavern.py 已删除）
- 运行: `python3 run.py --scene tavern --ticks 10 --mode interactive`
- 列出场景: `python3 run.py --list-scenes`
- 保存状态: `python3 run.py --scene tavern --ticks 10 --mode auto --save saves/run.json`
- 继续运行: `python3 run.py --load saves/run.json --ticks 5 --mode auto`（`--scene` 会和 `--load` 冲突）

## 硬性约定

以下规则任何改动都不能破坏，做相关任务前必须知晓：

- **新场景**：文件在 `scenarios/<name>.py`，类名 `<Title>Scene`（如 `TavernScene`）；必须实现 `name`、`locations`、`agents`、`gm_events`、`gm_random_events`、`render_config` 字段；Agent 配置必须含 `name`、`role`、`personality`、`goal`、`location`、`relationships`（启动时自动校验，缺字段立即报错）
- **新 Action**：文件放 `actions/<name>.py`，继承 `core.action.ActionSpec`，由场景的 `setup()`/`setup_gm()` 注册；核心代码不依赖具体动作
- **GM 工具白名单**：`setup_gm()` 全量声明所需工具（基类零依赖、不注册任何工具）；GM 用 `ActionRegistry(include_agent_params=False)`（不含 `internal_monologue` 和 `state_update`）
- **通用机制测试**统一用 `scenarios/_test.py` 的 `_TestScene`，不耦合生产场景（tavern/murder/spaceship）
- **场景自动识别**排除 `_` 开头的文件（如 `_test.py`）

详细规则见下方路由表对应文档，加载后同等效力。

## 文档路由（按需加载）

**CRITICAL**: 下面按需加载的文档用 Read 工具读取，不要预加载全部。加载后内容作为强制规范执行，与本文档同等效力。

| 任务 | 必读文档 |
|------|---------|
| 新增/修改场景、Action 注册与 tool schema、参数校验、连通性、渲染配置 | @docs/scenes.md |
| LLM 配置、tool_call/text_parse 双模式、重试、prompt 格式（text/chat） | @docs/prompt.md |
| MessageBus、可见性、环境状态、规则引擎、角色框架（Agent/NPC） | @docs/world.md |
| GM 事件注入、GM 工具、ReAct 循环、触发条件 | @docs/gm.md |
| Agent 流程（perceive/think/act）、模拟引擎、手动控制（ManualAgent）、记忆系统 | @docs/flow.md |
| config.yaml 配置、日志系统 | @docs/config.md |

## 测试

测试文件: `test/*.py`（`python3 -m pytest` 运行，配置见 `pytest.ini`）

- **离线集（默认）**: `pytest -q` 一键运行全部离线测试；`test_live_llm.py` 带 `llm` 标记（需 `.env` 中的 `DEEPSEEK_API_KEY` 真实调用），默认跳过
- **在线集**: `pytest -m llm` 运行需要 API key 的测试
- 共享脚手架在 `test/conftest.py`（离线配置、fake litellm 响应、手动计划写入）

| 测试文件 | 内容 |
|---------|------|
| `test_live_llm.py` | 真实 LLM：text_parse 解析、并发调用、Agent perceive/think（需 API key） |
| `test_regressions.py` | 回归：model 配置、记忆压缩、visibility 安全、message_bus、registry 防护 |
| `test_retry.py` | LLM 重试机制（无 tool call、不合法工具名、不合法参数） |
| `test_chat_format.py` | chat 模式消息结构、截断、retry 隔离、text 模式回归 |
| `test_engine.py` | SimulationEngine：tick 级与 Agent 级步进、规则触发、GM 注入、顺序轮换 |
| `test_gm.py` | GM 事件注入：计划事件确定性、随机事件概率 |
| `test_manual.py` | ManualAgent：无配置返回空、通配 tick、行动执行、非法行动返回空、文件错误 |
| `test_save_load.py` | 存档往返：Agent/GMAgent to_dict/from_dict 边界、格式稳定、版本迁移入口 |
| `test_tui_info.py` | TUI 信息格式化纯函数：工具列表、场景分节白名单、NPC 判断 |
| `test_world.py` | WorldState 位置索引：重建、副本语义、move_character/remove_npc 增量维护与自愈 |
| `test_npc.py` | 动态 NPC：Character 继承、AddNpcAction、npc_move、npc_remove、hearable/observe/speak 兼容、静态 NPC、存档往返 |

## 调试工具

### `scripts/print_prompt.py`

打印指定场景中 Agent 或 GM 的完整 system prompt，方便查看 LLM 实际收到的上下文，无需运行完整模拟：

```bash
python3 scripts/print_prompt.py spaceship           # 打印所有 Agent
python3 scripts/print_prompt.py tavern --agent 艾莉娅  # 只打印指定 Agent
python3 scripts/print_prompt.py tavern --gm          # 打印 GM system prompt + tick 0 世界上下文
```

GM 视角：Agent 标记为 `[Player]`（自主角色，禁止控制），NPC 标记为 `[NPC]`（由 GM 控制）。无 NPC 场景（如 spaceship）不标后缀，规则自动切换为"本场景没有 NPC，所有角色都是自主 Player"。

不需要 API key，毫秒级输出。`instruction` 字段的内容会出现在 prompt 末尾「输出要求」下方。
