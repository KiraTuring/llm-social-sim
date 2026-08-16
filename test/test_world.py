"""WorldState 位置索引测试：重建、副本语义、move_character 增量维护与自愈。"""

from core.world import WorldState, LocationGraph


class _StubAgent:
    """位置索引只依赖 location 与 content_max_length 的最小桩。"""

    def __init__(self, name: str, location: str):
        self.name = name
        self.location = location
        self.content_max_length = 200


class _StubNPC:
    """NPC 桩：move_character 对 NPC 生效只需 name 与 location。"""

    def __init__(self, name: str, location: str):
        self.name = name
        self.location = location


def _build_world() -> WorldState:
    """构造含 3 个 agent 的世界（不重建索引，模拟引擎启动前状态）。"""
    world = WorldState(geography=LocationGraph(locations=["主厅", "吧台", "角落"]))
    world.agents = {
        "老巴克": _StubAgent("老巴克", "吧台"),
        "雷恩": _StubAgent("雷恩", "角落"),
        "艾莉娅": _StubAgent("艾莉娅", "主厅"),
    }
    return world


def test_index_rebuild():
    """索引重建：分组正确，空位置返回 []"""
    world = _build_world()
    world.rebuild_location_index()
    assert world.get_agents_in_location("吧台") == ["老巴克"], world.get_agents_in_location("吧台")
    assert world.get_agents_in_location("主厅") == ["艾莉娅"]
    assert world.get_agents_in_location("角落") == ["雷恩"]
    assert world.get_agents_in_location("不存在的位置") == []

def test_get_agents_returns_copy():
    """副本语义：修改返回值不影响索引"""
    world = _build_world()
    world.rebuild_location_index()
    result = world.get_agents_in_location("吧台")
    result.remove("老巴克")
    result.append("伪造")
    assert world.get_agents_in_location("吧台") == ["老巴克"]

def test_move_character_incremental():
    """move_character 增量维护位置索引"""
    world = _build_world()
    world.rebuild_location_index()
    assert world.move_character("雷恩", "主厅") is None
    assert world.agents["雷恩"].location == "主厅"
    assert world.get_agents_in_location("主厅") == ["艾莉娅", "雷恩"]
    assert world.get_agents_in_location("角落") == []

def test_move_character_invalid():
    """非法名字/位置返回错误串且不移动"""
    world = _build_world()
    world.rebuild_location_index()
    err = world.move_character("不存在的人", "主厅")
    assert err is not None and "不存在" in err
    err = world.move_character("雷恩", "不存在的位置")
    assert err is not None and "不是有效位置" in err
    assert world.agents["雷恩"].location == "角落"
    assert world.get_agents_in_location("角落") == ["雷恩"]

def test_empty_index_self_heals():
    """索引清空后懒重建自愈"""
    world = _build_world()
    world._agents_by_location = {}
    assert world.get_agents_in_location("吧台") == ["老巴克"]
    world._agents_by_location = {}
    assert world.move_character("雷恩", "主厅") is None
    assert world.get_agents_in_location("主厅") == ["艾莉娅", "雷恩"]

def test_move_character_npc():
    """move_character 对 NPC 同样生效"""
    world = _build_world()
    world.npcs = {"巡逻兵": _StubNPC("巡逻兵", "主厅")}
    world.rebuild_location_index()
    assert world.move_character("巡逻兵", "吧台") is None
    assert world.npcs["巡逻兵"].location == "吧台"
    assert world.get_characters_in_location("吧台") == ["老巴克", "巡逻兵"]
    assert world.get_characters_in_location("主厅") == ["艾莉娅"]
    err = world.move_character("不存在的人", "吧台")
    assert err is not None and "不存在" in err

def test_remove_npc_sync_cleanup():
    """remove_npc：npcs/npc_names/索引三处同步清理"""
    world = _build_world()
    world.npcs = {"巡逻兵": _StubNPC("巡逻兵", "主厅")}
    world.npc_names = {"巡逻兵"}
    world.rebuild_location_index()
    assert world.remove_npc("巡逻兵") is None
    assert "巡逻兵" not in world.npcs and "巡逻兵" not in world.npc_names
    assert "巡逻兵" not in world.get_characters_in_location("主厅")
    err = world.remove_npc("巡逻兵")
    assert err is not None and "不是 NPC" in err

def test_validation_context_matches_index():
    """build_validation_context 与 get_agents_in_location 一致"""
    world = _build_world()
    world.rebuild_location_index()
    ctx = world.build_validation_context("老巴克")
    for loc in world.locations:
        assert ctx["agents_by_location"][loc] == world.get_agents_in_location(loc), loc
    assert ctx["hearable_agents"] == world.get_hearable_agents("老巴克")
