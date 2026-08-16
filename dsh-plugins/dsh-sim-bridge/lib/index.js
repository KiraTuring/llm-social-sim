'use strict'
/**
 * dsh-sim-bridge — LLM 社会模拟引擎的 DSH 插件（Host 半，web 组合行）。
 *
 * 提供 `simBridge` 服务（桥接进程 + JSONL 命令总线 + 世界状态）并注册
 * `/sim-bridge/rpc` HTTP 路由（Client 面板共用）。模型工具不在这里注册——
 * 它们在 `lib/tools.js`（preset 行，inject simBridge 后委托给本服务）。
 *
 * 平面：host 组合（web profile cordis.patch.yml）。桥接进程/世界为进程级单例，
 * 所有「社会模拟」会话与面板共享同一实例。
 *
 * 约定：
 * - 不发布到 isolate realm（这是 host 平面的共享服务）。
 * - 进程懒启动：只有 start/load/list_scenes 会 spawn；需要世界的命令未启动时
 *   直接报错而不 spawn；quit 后不复活；stop/卸载时 terminate 清理。
 */

const REPO_ROOT_FALLBACK = '/Users/haitongwang/Work/llm_playground'

// subprocess/webServer 是硬依赖：用 inject 声明，cordis 会 park 插件直到两者
// 就绪再执行 apply（patch 插入的 host 行在 root ctx 上可能先于 webServer 提供者
// 挂载，apply 时 ctx.get 会拿到 undefined——那是此前路由注册静默失败的根因）。
module.exports = {
  inject: ['subprocess', 'webServer'],
  apply(ctx) {
    const state = {
      child: null,
      chain: Promise.resolve(),
      seq: 0,
      worldActive: false,
      repoRoot: REPO_ROOT_FALLBACK,
      routeError: null,
    }

    const subprocess = ctx.subprocess
    const webServer = ctx.webServer
    // 桥接进程/世界是进程级单例：仓库路径是部署事实。不能用
    // sandboxPolicy.workspaceRoot（host 平面无会话，它只会回退成 HOME 目录）。

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

  // ---------- simBridge 服务 ----------

  // 需要世界的命令：未启动时直接报错（不 spawn）
  const WORLD_COMMANDS = new Set(['step', 'state', 'list_actions', 'inject_event', 'act_as', 'query_agent', 'save'])

  async function command(cmd, args) {
    if (WORLD_COMMANDS.has(cmd) && !state.worldActive) {
      throw new Error('尚未启动模拟：请先调用 sim_start（或 sim_load 从存档恢复）')
    }
    const data = await enqueue(cmd, args || {})
    if (cmd === 'start' || cmd === 'load') state.worldActive = true
    if (cmd === 'quit') state.worldActive = false
    return data
  }

  const simBridge = {
    command,
    state,
    diag: () => ({
      worldActive: state.worldActive,
      bridgeAlive: !!(state.child && !state.child.dead),
      repoRoot: state.repoRoot,
      routeError: state.routeError,
      bridgeStderrTail: stderrTail(),
    }),
  }
  ctx.provide('simBridge', simBridge)

  // ---------- HTTP 路由（Client 面板共用） ----------

  // webServer 已通过 inject 保证就绪；注册结果记入 state.routeError 供排障。
  try {
    const dispose = webServer.register({
      kind: 'exact',
      path: '/sim-bridge/rpc',
      async handler(req, res) {
        if (req.method !== 'POST') {
          res.writeHead(405, { 'content-type': 'text/plain; charset=utf-8' })
          res.end('POST only')
          return
        }
        let body = {}
        try {
          const chunks = []
          for await (const chunk of req) chunks.push(chunk)
          body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')
        } catch (e) {
          body = {}
        }
        const result = { ok: false }
        if (body && typeof body.cmd === 'string' && body.cmd) {
          const args = {}
          for (const k of Object.keys(body)) if (k !== 'cmd') args[k] = body[k]
          try {
            result.ok = true
            result.data = await simBridge.command(body.cmd, args)
          } catch (e) {
            result.ok = false
            result.error = String((e && e.message) || e)
          }
        } else {
          result.error = '缺少 cmd'
        }
        const text = JSON.stringify(result)
        res.writeHead(200, {
          'content-type': 'application/json; charset=utf-8',
          'content-length': Buffer.byteLength(text),
        })
        res.end(text)
      },
    })
    // cordis 的 ctx.effect(fn) 会立即调用 fn，并把 fn 的返回值当 teardown——
    // 因此必须包一层返回 disposer 的箭头函数，绝不能直接把 dispose 传进去。
    if (typeof dispose === 'function') ctx.effect(() => dispose)
    state.routeError = null
  } catch (e) {
    state.routeError = String((e && e.message) || e)
    ctx.logger.warn('[sim-bridge] register route failed: ' + state.routeError)
  }

  // ---------- 生命周期清理 ----------

  ctx.effect(() => () => {
    if (state.child && !state.child.dead) {
      try { state.child.handle.terminate() } catch (e) { /* ignore */ }
      state.child.dead = true
    }
  })
  },
}
