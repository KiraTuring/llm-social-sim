# 配置加载与日志

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

## 日志系统

### 日志文件

日志文件: `logs/simulation.log`（每次运行覆盖，配置见上文「日志配置」）

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
