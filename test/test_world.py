"""WorldState 位置索引测试：重建、副本语义、move_agent 增量维护与自愈。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.world import WorldState


class _StubAgent:
    """位置索引只依赖 location 与 content_max_length 的最小桩。"""

    def __init__(self, name: str, location: str):
        self.name = name
        self.location = location
        self.content_max_length = 200


def _build_world() -> WorldState:
    """构造含 3 个 agent 的世界（不重建索引，模拟引擎启动前状态）。"""
    world = WorldState(locations=["主厅", "吧台", "角落"])
    world.agents = {
        "老巴克": _StubAgent("老巴克", "吧台"),
        "雷恩": _StubAgent("雷恩", "角落"),
        "艾莉娅": _StubAgent("艾莉娅", "主厅"),
    }
    return world


def run_tests():
    print("测试 WorldState 位置索引")
    print("=" * 50)

    # 1. 索引重建：分组正确，空位置返回 []
    world = _build_world()
    world.rebuild_location_index()
    assert world.get_agents_in_location("吧台") == ["老巴克"], world.get_agents_in_location("吧台")
    assert world.get_agents_in_location("主厅") == ["艾莉娅"]
    assert world.get_agents_in_location("角落") == ["雷恩"]
    assert world.get_agents_in_location("不存在的位置") == []
    print("[1] 索引重建分组 OK")

    # 2. 副本语义：修改返回值不影响索引
    world = _build_world()
    world.rebuild_location_index()
    result = world.get_agents_in_location("吧台")
    result.remove("老巴克")
    result.append("伪造")
    assert world.get_agents_in_location("吧台") == ["老巴克"]
    print("[2] 返回副本语义 OK")

    # 3a. move_agent 增量维护
    world = _build_world()
    world.rebuild_location_index()
    assert world.move_agent("雷恩", "主厅") is None
    assert world.agents["雷恩"].location == "主厅"
    assert world.get_agents_in_location("主厅") == ["艾莉娅", "雷恩"]
    assert world.get_agents_in_location("角落") == []
    print("[3a] move_agent 增量维护 OK")

    # 3b. 非法名字/位置返回错误串且不移动
    world = _build_world()
    world.rebuild_location_index()
    err = world.move_agent("不存在的人", "主厅")
    assert err is not None and "不存在" in err
    err = world.move_agent("雷恩", "不存在的位置")
    assert err is not None and "不是有效位置" in err
    assert world.agents["雷恩"].location == "角落"
    assert world.get_agents_in_location("角落") == ["雷恩"]
    print("[3b] move_agent 非法参数 OK")

    # 3c. 索引清空后自愈重建
    world = _build_world()
    world._agents_by_location = {}
    assert world.get_agents_in_location("吧台") == ["老巴克"]
    world._agents_by_location = {}
    assert world.move_agent("雷恩", "主厅") is None
    assert world.get_agents_in_location("主厅") == ["艾莉娅", "雷恩"]
    print("[3c] 空索引懒重建自愈 OK")

    # 4. build_validation_context 与 get_agents_in_location 一致
    world = _build_world()
    world.rebuild_location_index()
    ctx = world.build_validation_context("老巴克")
    for loc in world.locations:
        assert ctx["agents_by_location"][loc] == world.get_agents_in_location(loc), loc
    assert ctx["hearable_agents"] == world.get_hearable_agents("老巴克")
    print("[4] validation_context 与索引一致 OK")

    print("=" * 50)
    print("全部 WorldState 位置索引测试通过")


run_tests()
