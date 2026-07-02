"""场景加载工具：动态加载场景类和发现可用场景。"""

import sys
from pathlib import Path


def load_scene(scene_name: str):
    """动态加载场景类"""
    try:
        module = __import__(f"scenarios.{scene_name}", fromlist=[scene_name.title()])
        scene_class = getattr(module, f"{scene_name.title()}Scene")
        return scene_class()
    except (ImportError, AttributeError) as e:
        print(f"❌ 无法加载场景 '{scene_name}': {e}")
        sys.exit(1)


def list_available_scenes():
    """列出所有可用场景"""
    scenes_dir = Path(__file__).parent
    scene_files = list(scenes_dir.glob("*_scene.py")) + list(scenes_dir.glob("[!_]*.py"))

    scenes = []
    for f in scene_files:
        if f.name.startswith("_"):
            continue
        if f.name == "base.py" or f.name == "utils.py":
            continue
        scene_name = f.stem.replace("_scene", "")
        scenes.append(scene_name)

    return sorted(scenes)
