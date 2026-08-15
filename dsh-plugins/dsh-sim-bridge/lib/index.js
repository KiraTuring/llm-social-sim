'use strict'
/**
 * dsh-sim-bridge — LLM 社会模拟引擎的 DSH 持久插件（Host 半）。
 *
 * 注册 sim_* 模型工具（sim_list_scenes / sim_start / sim_step / sim_state /
 * sim_list_actions / sim_inject_event / sim_act_as / sim_query_agent / sim_save /
 * sim_load / sim_quit / sim_diag），通过 `scripts/sim_bridge.py`（JSONL over
 * stdio）桥接本仓库的模拟引擎。作为 agent preset 的一行挂载：会话级、随 preset 持久。
 *
 * 约定：
 * - 不发布任何 Service，只注册模型工具 → 不需要 isolate realm。
 * - 使用 ctx.subprocess（host 平面的抽象服务）拉起桥接进程，尊重执行世界。
 * - 进程懒启动：只有 start/load/list_scenes 会 spawn；需要世界的命令在未启动时
 *   直接报错而不 spawn；quit 后不复活；stop/卸载时 terminate 清理。
 */

const REPO_ROOT_FALLBACK = '/Users/haitongwang/Work/llm_playground'

module.exports = function simBridgePlugin(ctx) {
  const state = {
    child: null,
    chain: Promise.resolve(),
    seq: 0,
    worldActive: false,
    repoRoot: REPO_ROOT_FALLBACK,
    registerErrors: [],
  }

  const subprocess = ctx.get('subprocess')
  const sandboxPolicy = ctx.get('sandboxPolicy')
  if (sandboxPolicy && sandboxPolicy.workspaceRoot) state.repoRoot = sandboxPolicy.workspaceRoot

  // 工作区门控：sim 工具只在本仓库工作区生效。DSH 的 preset 是部署级全局的
  // （list() 扫全部 roots、picker 无工作区过滤），无法让 preset 只出现在某个
  // 工作区；这里改为按会话 workspaceRoot 自检——别的项目里挂载本 preset 时
  // 不注册任何 sim_* 工具，避免无关项目出现无关工具。
  const normalize = (p) => String(p || '').replace(/[\\/]+$/, '')
  const ALLOWED_WORKSPACE = normalize(REPO_ROOT_FALLBACK)
  if (normalize(state.repoRoot) !== ALLOWED_WORKSPACE) return

  // ---------- 桥接进程（JSONL over stdio） ----------

  function makeLineReader(readable) {
    let buffer = ''
    let ended = false
    const waiters = []
    if (readable && typeof readable.setEncoding === 'function') readable.setEncoding('utf8')
    const onData = (chunk) => {
      buffer += String(chunk)
      while (true) {
        const idx = buffer.indexOf('\n')
        if (idx < 0) break
        const line = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 1)
        if (waiters.length) waiters.shift()(line)
      }
    }
    const onEnd = () => { ended = true; while (waiters.length) waiters.shift()(null) }
    if (readable) {
      readable.on('data', onData)
      readable.on('end', onEnd)
      readable.on('error', onEnd)
    }
    return function readLine() {
      const idx = buffer.indexOf('\n')
      if (idx >= 0) {
        const line = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 1)
        return Promise.resolve(line)
      }
      if (ended) return Promise.resolve(null)
      return new Promise((resolve) => waiters.push(resolve))
    }
  }

  async function spawnChild() {
    if (state.child && !state.child.dead) return state.child
    if (subprocess === undefined) throw new Error('subprocess 服务不可用（无法拉起模拟进程）')
    let python
    try {
      python = await subprocess.resolveExecutable('python3')
    } catch (e) {
      throw new Error('无法解析 python3: ' + ((e && e.message) || e))
    }
    const handle = subprocess.spawn({
      argv: [python, 'scripts/sim_bridge.py'],
      cwd: state.repoRoot,
      stdio: {
        stdin: 'pipe',
        stdout: 'pipe',
        stderr: { maxBytes: 262144, spill: { maxBytes: 1048576 } },
      },
      graceMs: 5000,
    })
    // Node 中未监听的流 'error' 事件是致命的（子进程被杀/管道断裂会抛崩进程）。
    if (handle.stdin) handle.stdin.on('error', function () { /* swallow EPIPE */ })
    if (handle.stdout) handle.stdout.on('error', function () { /* swallow */ })
    const child = { handle, dead: false, readLine: makeLineReader(handle.stdout) }
    handle.done.then((outcome) => {
      child.dead = true
      ctx.logger.info(`[sim-bridge] bridge exited code=${outcome.exitCode} signal=${outcome.signal}`)
    }).catch((e) => {
      child.dead = true
      ctx.logger.warn('[sim-bridge] bridge spawn failed: ' + ((e && e.message) || e))
    })
    state.child = child
    return child
  }

  async function runCommand(cmd, args) {
    const child = await spawnChild()
    if (child.dead) throw new Error('桥接进程已退出，无法执行 ' + cmd)
    const reqId = ++state.seq
    const payload = JSON.stringify(Object.assign({ req_id: reqId, cmd }, args))
    const respPromise = child.readLine().then((line) => {
      if (line === null) throw new Error('桥接进程已退出，无法执行 ' + cmd)
      let parsed
      try { parsed = JSON.parse(line) } catch (e) { throw new Error('桥接响应非法: ' + String(line).slice(0, 120)) }
      return parsed
    })
    try {
      await new Promise((resolve, reject) => {
        child.handle.stdin.write(payload + '\n', 'utf8', (err) => err ? reject(err) : resolve())
      })
    } catch (e) {
      throw new Error('写入桥接失败: ' + ((e && e.message) || e))
    }
    const resp = await respPromise
    if (!resp.ok) throw new Error(resp.error || ('命令失败: ' + cmd))
    return resp.data
  }

  function enqueue(cmd, args) {
    const run = state.chain.then(() => runCommand(cmd, args))
    state.chain = run.then(() => undefined, () => undefined)
    return run
  }

  function stderrTail() {
    const c = state.child
    if (!c || c.dead) return ''
    try {
      const reader = c.handle && c.handle.collected && c.handle.collected.stderr
      if (!reader) return ''
      const read = reader.readFrom(0)
      const text = (read && read.text) || ''
      return text.slice(-2000)
    } catch (e) {
      return ''
    }
  }

  // ---------- 工具注册 ----------

  const renderText = (args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }]
  const output = { schema: {}, render: renderText }

  // opts: { requiresWorld?: boolean, execute?: (args, exec) => Promise }
  function defineTool(cmd, name, description, parameters, opts) {
    const requiresWorld = !!(opts && opts.requiresWorld)
    const executeOverride = opts && opts.execute
    const definition = {
      name,
      description,
      parameters,
      output,
      async execute(args, exec) {
        if (requiresWorld && !state.worldActive) {
          throw new Error('尚未启动模拟：请先调用 sim_start（或 sim_load 从存档恢复）')
        }
        const run = executeOverride
          ? executeOverride(args, exec)
          : enqueue(cmd, args || {})
        if (exec && exec.signal && !executeOverride) {
          return Promise.race([run, new Promise((_, reject) => {
            const onAbort = () => { exec.signal.removeEventListener('abort', onAbort); reject(new Error('工具调用已取消')) }
            if (exec.signal.aborted) { reject(new Error('工具调用已取消')); return }
            exec.signal.addEventListener('abort', onAbort, { once: true })
          })])
        }
        return run
      },
    }
    try {
      ctx.tools.register(definition)
    } catch (e) {
      const msg = ((e && e.message) || e)
      state.registerErrors.push(name + ': ' + msg)
      ctx.logger.warn('[sim-bridge] register tool ' + name + ' failed: ' + msg)
    }
  }

  const S = (type, description, required) => {
    const node = { type, description }
    if (required) node.required = true
    return node
  }

  defineTool('list_scenes', 'sim_list_scenes',
    '列出所有可用的社会模拟场景（如 tavern / murder / spaceship）',
    { type: 'object', properties: {} })

  defineTool('start', 'sim_start',
    '启动一个新的社会模拟世界（LLM 驱动，角色按人格/目标自主行动，GM 注入事件）',
    {
      type: 'object',
      properties: {
        scene: S('string', '场景名，先用 sim_list_scenes 查看'),
        config_path: S('string', '可选：config.yaml 路径（默认仓库根目录）'),
        manual_agents: { type: 'array', description: '可选：手动控制的角色名（不调用 LLM）', items: { type: 'string' } },
        manual_file: S('string', '可选：手动控制计划 JSON 文件路径'),
      },
      required: ['scene'],
    }, {
      execute: async (args) => {
        const data = await enqueue('start', args || {})
        state.worldActive = true
        return data
      },
    })

  defineTool('step', 'sim_step',
    '推进模拟 N 个 tick（默认 1），返回每个角色的行动与消息',
    { type: 'object', properties: { ticks: S('integer', '推进的 tick 数，>= 1') } },
    { requiresWorld: true })

  defineTool('state', 'sim_state',
    '查看当前世界快照：tick、场景、角色位置/状态、可用行动、最近消息、事件日志',
    { type: 'object', properties: {} }, { requiresWorld: true })

  defineTool('list_actions', 'sim_list_actions',
    '列出当前场景注册给角色的全部行动类型（供 sim_act_as 选用）',
    { type: 'object', properties: {} }, { requiresWorld: true })

  defineTool('inject_event', 'sim_inject_event',
    '以 GM 身份向世界注入一条外部事件（广播给所有角色，可选同步修改环境状态）',
    {
      type: 'object',
      properties: {
        content: S('string', '事件内容，一句话'),
        environment: {
          type: 'object',
          description: '可选：同时更新某个位置的环境状态',
          properties: {
            location: S('string', '环境位置名'),
            key: S('string', '环境指标 key'),
            value: S('string', '环境指标值'),
          },
        },
      },
      required: ['content'],
    }, { requiresWorld: true })

  defineTool('act_as', 'sim_act_as',
    '替指定角色安排下一次行动（在下一 tick 该角色行动时执行，非法则回退观察）',
    {
      type: 'object',
      properties: {
        agent: S('string', '要控制的角色名'),
        action_type: S('string', '行动类型，先用 sim_list_actions 查看可用值'),
        target: S('string', '行动目标（角色名或位置）'),
        content: S('string', '行动内容'),
        internal_monologue: S('string', '内心独白（其他角色不可见）'),
        params: { description: '额外行动参数（如状态修改）' },
      },
      required: ['agent', 'action_type'],
    }, { requiresWorld: true })

  defineTool('query_agent', 'sim_query_agent',
    '查看某个角色的档案：性格、目标、位置、关系、最近记忆',
    { type: 'object', properties: { agent: S('string', '角色名') }, required: ['agent'] },
    { requiresWorld: true })

  defineTool('save', 'sim_save',
    '保存当前世界状态到存档文件',
    { type: 'object', properties: { path: S('string', '存档路径（默认 saves/bridge_run.json）') } },
    { requiresWorld: true })

  defineTool('load', 'sim_load',
    '从存档文件恢复世界并继续运行',
    { type: 'object', properties: { path: S('string', '存档文件路径') }, required: ['path'] }, {
      execute: async (args) => {
        const data = await enqueue('load', args || {})
        state.worldActive = true
        return data
      },
    })

  defineTool('quit', 'sim_quit',
    '关闭模拟进程，释放 LLM 连接与日志句柄',
    { type: 'object', properties: {} }, {
      execute: async () => {
        const data = await enqueue('quit', {})
        state.worldActive = false
        if (state.child) {
          try { state.child.handle.terminate() } catch (e) { /* ignore */ }
          state.child.dead = true
        }
        return data
      },
    })

  defineTool('diag', 'sim_diag',
    '诊断：报告插件运行状态（桥接进程、世界是否启动、注册错误、桥接 stderr 尾部）',
    { type: 'object', properties: {} }, {
      execute: async () => ({
        worldActive: state.worldActive,
        bridgeAlive: !!(state.child && !state.child.dead),
        repoRoot: state.repoRoot,
        registerErrors: state.registerErrors,
        bridgeStderrTail: stderrTail(),
      }),
    })

  // 生命周期清理：插件停止/卸载时 terminate 子进程
  ctx.effect(() => {
    if (state.child && !state.child.dead) {
      try { state.child.handle.terminate() } catch (e) { /* ignore */ }
      state.child.dead = true
    }
  })
}
