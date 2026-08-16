"""场景包：场景定义 + 场景发现/加载入口。

场景包自己负责「发现自己、加载自己」；core 不 import scenarios/。
新增场景：在 scenarios/<name>.py 中写 <Title>Scene 类，并在 setup()/setup_gm()
中注册所需 Action 即可，无需修改 core。
"""

import sys
from pathlib import Path


def load_scene(scene_name: str):
    """动态加载场景类。

    约定：模块 scenarios/<scene_name>.py，类名 <Title>Scene。
    """
    try:
        module = __import__(f"scenarios.{scene_name}", fromlist=[scene_name.title()])
        scene_class = getattr(module, f"{scene_name.title()}Scene")
        return scene_class()
    except (ImportError, AttributeError) as e:
        print(f"❌ 无法加载场景 '{scene_name}': {e}")
        sys.exit(1)


def list_available_scenes(scenes_dir: Path | None = None) -> list[str]:
    """列出所有可用场景（排除 _ 开头的文件，如 _test.py）。"""
    if scenes_dir is None:
        scenes_dir = Path(__file__).parent
    return sorted(f.stem for f in scenes_dir.glob("[!_]*.py"))
