# dsh-sim-bridge

LLM 社会模拟引擎（本仓库）的 DSH 持久插件。作为 agent preset 的一行挂载，
为会话提供 `sim_*` 模型工具，通过 `scripts/sim_bridge.py`（JSONL over stdio）
桥接模拟引擎。

## 组成

- `lib/index.js` — Host 半插件（CommonJS，cordis plugin 函数）：
  注册 `sim_list_scenes / sim_start / sim_step / sim_state / sim_list_actions /
  sim_inject_event / sim_act_as / sim_query_agent / sim_save / sim_load /
  sim_quit / sim_diag`，用 `ctx.subprocess` 拉起桥接进程，懒启动、需要世界的
  命令未启动时不 spawn、quit 后不复活、stop 时 terminate。
  只注册模型工具、不发布服务 → 组合行无需 isolate realm。
- `package.json` — 包名 `dsh-sim-bridge`，零运行时依赖（raw JSON Schema 手写）。

## 安装（一次性）

组合 loader 只按包名从 profile 的 node_modules 解析，因此需要把本目录链接进
profile 安装（DSH 重启不丢，pnpm 重装 profile 后需重跑）：

```bash
ln -sfn <repo>/dsh-plugins/dsh-sim-bridge ~/.dsh/profiles/web/node_modules/dsh-sim-bridge
```

## 工作区门控

DSH 的 preset 是部署级全局的（`agentPresets.list()` 扫全部 roots，picker 无工作区
过滤），无法让 preset 只出现在某个工作区。因此插件按会话的
`sandboxPolicy.workspaceRoot` 自检：仅当等于 `llm_playground` 时注册 sim_* 工具，
其他工作区的会话挂载本 preset 时注册为空（能力上等价于"只在仓库工作区可用"）。

## Preset

`~/.dsh/.agent-presets/sim-bridge/agent.cordis.yml` 由 `standard` 复制而来，
末尾追加一行：

```yaml
- id: tool-sim-bridge
  name: dsh-sim-bridge
```

校验：`agentPresets.standingKeyFor('sim-bridge')` 返回 mounted OK。
新会话选择「社会模拟」preset 即获得全部 sim_* 工具（随 preset 持久，重启不丢）。
