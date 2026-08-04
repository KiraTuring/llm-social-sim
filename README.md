# LLM 社会模拟引擎

用多个 LLM Agent 进行社会模拟，观察自发演化故事。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

# 运行默认场景（酒馆）
python3 run.py

# 或指定场景、tick 数和模式
python3 run.py --scene tavern --ticks 20 --mode interactive
```

## 命令行参数

| 参数 | 简写 | 说明 | 示例 |
|---|---|---|---|
| `--scene` | `-s` | 场景名称 | `--scene tavern` |
| `--ticks` | `-t` | 运行 tick 数 | `--ticks 20` |
| `--mode` | `-m` | 运行模式 | `--mode interactive` |
| `--list-scenes` | `-l` | 列出所有场景 | `--list-scenes` |
| `--config` | `-c` | 配置文件路径 | `--config my_config.yaml` |
| `--manual` | | 手动控制的 Agent 名称（多个用空格分隔） | `--manual 老巴克` |
| `--manual-file` | | 手动控制 JSON 文件路径 | `--manual-file my_actions.json` |
| `--save` | | 运行结束后保存状态到文件 | `--save saves/run.json` |
| `--load` | | 从存档继续运行（与 `--scene` 互斥） | `--load saves/run.json` |
| `--tui` | | 使用 Textual TUI 界面 | `--tui` |

### 示例

```bash
# 交互式运行 10 个 tick
python3 run.py --scene tavern --ticks 10 --mode interactive

# 自动运行 20 个 tick
python3 run.py -s tavern -t 20 -m auto

# 列出所有可用场景
python3 run.py --list-scenes
```

## 场景

当前可用场景：

- **tavern** — 破釜酒馆（3 角色：酒馆老板、佣兵、神秘旅人）

### 创建新场景

在 `scenarios/` 下创建新文件，继承 `Scene` 基类：

```python
# scenarios/myscene.py
from scenarios.base import Scene

class MyScene(Scene):
    name = "我的场景"
    locations = ["地点A", "地点B"]
    
    agents = [
        {
            "name": "角色1",
            "role": "身份",
            "personality": "性格描述",
            "goal": "目标",
            "location": "地点A",
            "relationships": {},
        },
    ]
    
    gm_events = [(3, "tick3 发生的事件")]
    gm_random_events = ["随机事件1", "随机事件2"]
```

运行：
```bash
python3 run.py --scene myscene
```

## 手动控制（测试用）

让指定 Agent 不走 LLM、按 JSON 文件执行预设行动，适合确定性测试场景：

```bash
python3 run.py --scene tavern --ticks 5 --mode auto --manual 老巴克 --manual-file manual_actions.example.json
```

JSON 结构为「角色 → tick → 行动」，`"*"` 是通配 tick（未单独配置时重复执行，优先级低于具体 tick）：

```json
{
  "老巴克": {
    "1": {"action_type": "speak", "target": "艾莉娅", "content": "欢迎光临"},
    "*": {"action_type": "observe", "content": "环顾酒馆"}
  }
}
```

未配置的 tick 自动 `observe`；行动非法（未知 action_type、目标不可达等）会记 warning 并回退为 `observe`。文件缺失或格式错误会在启动时直接报错。

## 配置

编辑 `config.yaml` 调整 LLM、模拟、Agent、GM 设置。

### 日志配置

日志默认写入 `logs/simulation.log`，每次运行覆盖。可通过 `config.yaml` 调整：

```yaml
logging:
  file: "logs/simulation.log"  # 日志文件路径
  level: "INFO"                # 日志级别: DEBUG, INFO, WARNING, ERROR
```

日志记录内容：
- LLM 完整交互（提示词、原始回复、解析结果）
- 模拟流程（tick 边界、agent 行动、消息流）

## 文档

- `core/` — 核心逻辑（世界、Agent、Action、规则、日志）
- `llm/` — LLM 调用层
- `memory/` — 记忆系统
- `render/` — 控制台渲染
- `scenarios/` — 场景定义
- `test/` — 测试脚本
