"""测试 GM Agent 事件注入。"""

import asyncio
from core.gm import GMAgent
from core.world import WorldState
from core.message import MessageBus


async def test_gm():
    """测试 GM Agent 事件注入"""

    world = WorldState(tick=1, locations=["酒馆"])
    world.message_bus = MessageBus()

    gm = GMAgent(
        events=[(2, "一个穿黑甲的士兵推门进来"), (4, "外面传来马蹄声")],
        random_events=["醉汉开始唱歌", "壁炉火噼啪作响"],
        chance=0.3,
    )

    print("测试 GM 事件注入")
    print("=" * 50)

    for tick in range(1, 6):
        world.tick = tick

        await gm.check_and_inject(world)

        if world.event_log:
            print(f"Tick {tick}: {world.event_log[-1]}")

    print("\n测试完成 ✅")


if __name__ == "__main__":
    asyncio.run(test_gm())