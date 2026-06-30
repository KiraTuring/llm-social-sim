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

## 配置

编辑 `config.yaml` 调整 LLM、模拟、Agent、GM 设置。

## 文档

- `core/` — 核心逻辑（世界、Agent、Action、规则）
- `llm/` — LLM 调用层
- `memory/` — 记忆系统
- `render/` — 控制台渲染
- `scenarios/` — 场景定义
- `test/` — 测试脚本