# dsh-sim-bridge

LLM 社会模拟引擎（本仓库）的 DSH 持久插件。**单包双面 + 双平面**：

- **host 组合行**（`~/.dsh/profiles/web/cordis.patch.yml` 的 `sim-bridge-host`）：
  `lib/index.js` 提供 `simBridge` 服务（桥接进程 + JSONL 命令总线 + 世界状态）、
  注册 `/sim-bridge/rpc` HTTP 路由，并声明 `dsh.client` 让 clientModules 提供
  `lib/client.js` 实时面板 bundle（渲染在 `conversation.input.dock`）。
- **preset 行**（`社会模拟` preset 的 `tool-sim-bridge` → `dsh-sim-bridge/tools`）：
  `lib/tools.js` `inject: ['simBridge']`，注册 12 个 sim_* 模型工具委托给该服务。

世界是**进程级单例**（所有「社会模拟」会话与面板共享同一桥接进程/世界）。

## 文件

| 文件 | 作用 |
|---|---|
| `lib/index.js` | host 半：simBridge 服务 + `/sim-bridge/rpc` 路由（inject `subprocess`/`webServer` 硬依赖；`ctx.effect(() => disposer)` 包裹清理） |
| `lib/tools.js` | preset 半：12 个 sim_* 工具（含 `sim_list_actions`、`sim_diag`），委托 `ctx.simBridge.command` |
| `lib/client.js` | 面板 bundle（`__ModuleLoader__` 格式 + React + fetch RPC + 3s 轮询） |
| `package.json` | main + `./tools`/`./client` exports + `dsh.client` 声明 |

## 安装（一次性）

1. 链接进 profile（pnpm 重装后需重跑）：
   ```bash
   ln -sfn <repo>/dsh-plugins/dsh-sim-bridge ~/.dsh/profiles/web/node_modules/dsh-sim-bridge
   ```
2. host 行已写入 `~/.dsh/profiles/web/cordis.patch.yml`（`- insert: [{id: sim-bridge-host, name: dsh-sim-bridge}]`）。
3. preset 行已写入 `~/.dsh/.agent-presets/sim-bridge/agent.cordis.yml`（`name: dsh-sim-bridge/tools`）。
4. **重启 DSH**：host 组合与 clientModules 首次扫描都依赖重启；浏览器刷新页面加载新 boot manifest 后，面板出现在输入框上方。

## 配置

host 行支持 `config.panelEnabled`（默认 `true`）：设为 `false` 时 Client 面板整体不渲染、不轮询（工具与 `/sim-bridge/rpc` 不受影响）。改配置需重启 DSH 生效。

```yaml
- insert:
    - id: sim-bridge-host
      name: dsh-sim-bridge
      config:
        panelEnabled: false
```

## 排障

- `curl -X POST -H 'content-type: application/json' -d '{"cmd":"list_scenes"}' http://127.0.0.1:3080/sim-bridge/rpc` — 路由存活检查。
- 路由/服务状态：`sim_diag` 工具返回 `routeError`、`bridgeAlive`、`worldActive` 等。
- **已踩过的坑（勿回退）**：
  - `lib/tools.js` 的 `inject` 必须同时声明 `'simBridge'` **和 `'tools'`**：cordis 对未 inject 的服务属性访问会抛 `cannot get property "tools" without inject`。缺 `tools` 时 `ctx.tools.register` 的异常被 defineTool 的 try/catch 吞掉，12 个 sim_* 工具全部静默注册失败，而 apply 正常返回 → preset 挂载照常成功、persona 生效，唯独工具缺失（表现为：系统提示有「社会模拟」指南但工具列表没有 sim_*，路由 `/sim-bridge/rpc` 却正常）。
  - host 行不能用 `ctx.get('webServer')`（挂载时序问题，用 inject）；`ctx.effect` 传 disposer 要包箭头函数（setup/teardown 语义）；host 平面 `sandboxPolicy.workspaceRoot` 会回退成 HOME，仓库路径用常量。
