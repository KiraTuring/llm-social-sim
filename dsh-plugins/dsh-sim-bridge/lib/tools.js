'use strict'
/**
 * dsh-sim-bridge — 模型工具半（preset 行）。
 *
 * `inject: ['simBridge', 'tools']`：simBridge 解析 host 平面的 simBridge 服务，
 * `tools` 声明工具注册表（cordis 对未 inject 的服务属性访问会拦截——缺 `tools`
 * 时 `ctx.tools.register` 抛 `cannot get property "tools" without inject`，被
 * 下方 try/catch 吞掉后 12 个 sim_* 工具会全部静默注册失败，而 apply 仍正常
 * 返回，preset 挂载照常成功、persona 生效，唯独工具缺失）。
 * 注册 12 个 sim_* 工具，全部委托给 `ctx.simBridge.command(cmd, args)`。
 * 不发布任何服务 → 组合行无需 isolate realm。仅「社会模拟」preset 的会话可见。
 */

module.exports = {
  inject: ['simBridge', 'tools'],
  apply(ctx) {
    const simBridge = ctx.simBridge
    const registerErrors = []

    const renderText = (args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }]
    const output = { schema: {}, render: renderText }

    function defineTool(cmd, name, description, parameters, executeOverride, toolOutput) {
      const definition = {
        name,
        description,
        parameters,
        output: toolOutput || output,
        async execute(args, exec) {
          const run = executeOverride
            ? executeOverride(args, exec)
            : simBridge.command(cmd, args || {})
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
        registerErrors.push(name + ': ' + msg)
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
      })

    defineTool('step', 'sim_step',
      '推进模拟 N 个 tick（默认 1），返回每个角色的行动与消息；view=narrative 时返回可读剧情文本',
      {
        type: 'object',
        properties: {
          ticks: S('integer', '推进的 tick 数，>= 1'),
          view: S('string', 'raw（默认，返回结构化 JSON）| narrative（返回可读剧情文本）'),
        },
      },
      undefined,
      {
        schema: {},
        render: (args, value) => {
          if (args && args.view === 'narrative' && value && value.narrative) {
            return [{ type: 'text', text: value.narrative }]
          }
          return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
        },
      })

    defineTool('state', 'sim_state',
      '查看当前世界快照：tick、场景、角色位置/状态、可用行动、最近消息、事件日志',
      { type: 'object', properties: {} })

    defineTool('list_actions', 'sim_list_actions',
      '列出当前场景注册给角色的全部行动类型（供 sim_act_as 选用）',
      { type: 'object', properties: {} })

    defineTool('inject_event', 'sim_inject_event',
      '以 GM 身份注入外部事件。默认广播给所有角色；target 可定向到角色（私信）或位置（该位置及可见位置的人）；可选同步更新一个或多个环境状态',
      {
        type: 'object',
        properties: {
          content: S('string', '事件内容，一句话'),
          target: S('string', '可选：接收目标——留空=广播；角色名=私信该角色；位置名=发给身处该位置及能看到该位置的所有人'),
          environment: {
            type: 'object',
            description: '可选：环境状态更新。传单个对象 {location,key,value} 更新一处，或传对象数组 [{location,key,value},...] 批量更新多处',
            properties: {
              location: S('string', '环境位置名'),
              key: S('string', '环境指标 key'),
              value: S('string', '环境指标值'),
            },
          },
        },
        required: ['content'],
      })

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
      })

    defineTool('query_agent', 'sim_query_agent',
      '查看某个角色的档案：性格、目标、位置、关系、最近记忆',
      { type: 'object', properties: { agent: S('string', '角色名') }, required: ['agent'] })

    defineTool('save', 'sim_save',
      '保存当前世界状态到存档文件',
      { type: 'object', properties: { path: S('string', '存档路径（默认 saves/bridge_run.json）') } })

    defineTool('load', 'sim_load',
      '从存档文件恢复世界并继续运行',
      { type: 'object', properties: { path: S('string', '存档文件路径') }, required: ['path'] })

    defineTool('quit', 'sim_quit',
      '关闭模拟进程，释放 LLM 连接与日志句柄',
      { type: 'object', properties: {} })

    defineTool('diag', 'sim_diag',
      '诊断：报告插件运行状态（桥接进程、世界是否启动、注册错误、桥接 stderr 尾部）',
      { type: 'object', properties: {} }, async () => Object.assign(
        simBridge.diag(),
        { registerErrors },
      ))
  },
}
