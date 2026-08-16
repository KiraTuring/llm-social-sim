"""贸易 Action：同一位置的 Agent 间（含 NPC）转移金钱/物品。

状态约定：
- 钱包统一存在 states[INVENTORY_KEY]（见 core.world.INVENTORY_KEY）；
  内部资源名由场景自定义，trade 沿用 金钱=整数、物品={名称: 数量} 的约定
- 事件记录由引擎统一处理（Agent 行动写入 event_log），本动作不重复记录

give 是行动者付出的（钱或物品），take 是行动者获得的。take 必须伴随 give
（有来有往）；纯 give（支付/送礼）允许。旁观者能看到交易的物品，看不到金额。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.action import ActionSpec, validate_content_length
from core.message import Message
from core.world import INVENTORY_KEY

if TYPE_CHECKING:
    from core.world import WorldState

# 钱包（states[INVENTORY_KEY]）内部的资源名约定：金钱=金额(int)，物品={名称: 数量}
MONEY_KEY, ITEMS_KEY = "金钱", "物品"


def _wallet(states: dict) -> dict:
    """获取（或初始化）角色的钱包字典 states[INVENTORY_KEY]。"""
    return states.setdefault(INVENTORY_KEY, {})


class TradeAction(ActionSpec):
    name = "trade"
    icon = "🤝"
    description = (
        "与同一位置的另一个角色交易：give 是你付出的（金钱或物品），take 是你获得的（金钱或物品）。"
        "take 必须伴随 give（有来有往），纯 give（支付/送礼）允许。旁观者能看到交易的物品，看不到金额"
    )
    text_format = "[ACTION]trade[/ACTION]\n[TARGET]{交易对象}[/TARGET]\n[CONTENT]{交易描述，可选}[/CONTENT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "交易对象（同位置的另一个角色，Agent 或 NPC）"},
                        "give_money": {"type": "integer", "description": "你付出的金钱数量（可选）"},
                        "give_items": {"type": "object", "description": "你付出的物品（可选），如 {\"酒壶\": 1}"},
                        "take_money": {"type": "integer", "description": "你希望获得的金钱数量（可选）"},
                        "take_items": {"type": "object", "description": "你希望获得的物品（可选），如 {\"干粮\": 2}"},
                        "content": {"type": "string", "description": "交易时的行为表现（可选，别人能看到，如「把一枚银币放在柜台上」）"},
                    },
                    "required": ["target"],
                },
            },
        }

    def validate_params(self, params: dict, context: dict) -> str | None:
        """校验交易参数：对象、同位置、give/take 规则、支付能力、内容长度。"""
        target = params.get("target", "")
        agent_name = context.get("agent_name", "")
        agent_names = context.get("agent_names", [])
        if not target:
            return "请指定交易对象"
        if target == agent_name:
            return "不能和自己交易"
        if target not in agent_names:
            return f"'{target}' 不存在，可用的交易对象: {', '.join(agent_names)}"
        agent_loc = context.get("agent_location", "")
        if target not in context.get("agents_by_location", {}).get(agent_loc, []):
            return f"'{target}' 不在你当前的位置({agent_loc})，无法交易"

        give_money = params.get("give_money", 0)
        give_items = params.get("give_items") or {}
        take_money = params.get("take_money", 0)
        take_items = params.get("take_items") or {}
        if error := self._validate_amounts(give_money, give_items, take_money, take_items):
            return error

        # 有来有往：take 必须伴随 give；纯 give（支付/送礼）允许
        giving = give_money > 0 or bool(give_items)
        taking = take_money > 0 or bool(take_items)
        if not giving and not taking:
            return "交易内容为空：请至少付出或获得一些东西"
        if taking and not giving:
            return "有来有往：要获得物品/金钱（take），必须先付出（give）"

        # 行动者支付能力（inventory 只含自己的经济状态，见 build_validation_context）
        inventory = context.get("inventory") or {}
        wallet = inventory.get(MONEY_KEY, 0)
        if give_money > wallet:
            return f"金钱不足：你只有 {wallet}，无法付出 {give_money}"
        stock = inventory.get(ITEMS_KEY) or {}
        for name, qty in give_items.items():
            have = stock.get(name, 0)
            if qty > have:
                return f"物品不足：你只有 {name}×{have}，无法付出 {name}×{qty}"

        if error := validate_content_length(params.get("content", ""), context):
            return error
        return None

    @staticmethod
    def _validate_amounts(give_money, give_items, take_money, take_items) -> str | None:
        """数值合法性：金钱为非负整数，物品数量为正整数。"""
        for label, value in (("give_money", give_money), ("take_money", take_money)):
            if not isinstance(value, int) or isinstance(value, bool):
                return f"{label} 必须是整数"
            if value < 0:
                return f"{label} 不能为负"
        for label, items in (("give_items", give_items), ("take_items", take_items)):
            if not items:
                continue
            if not isinstance(items, dict):
                return f"{label} 必须是 {{名称: 数量}} 形式的对象"
            for name, qty in items.items():
                if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
                    return f"{label} 中 '{name}' 的数量必须是正整数"
        return None

    def execute(self, agent_name: str, params: dict, world: "WorldState"):
        """执行交易：防御性双检 → 转移双方账目 → 私信对手方 + 通知旁观者。"""
        target = params.get("target", "")
        try:
            give_money = int(params.get("give_money") or 0)
            take_money = int(params.get("take_money") or 0)
        except (TypeError, ValueError):
            return [], {"summary": "金额必须是整数"}
        give_items = params.get("give_items") or {}
        take_items = params.get("take_items") or {}
        if not isinstance(give_items, dict) or not isinstance(take_items, dict):
            return [], {"summary": "物品参数格式错误，应为 {名称: 数量}"}

        actor = world.agents[agent_name]
        counterparty = world.characters.get(target)
        if counterparty is None:
            return [], {"summary": f"未找到角色 {target}"}

        # 防御性双检（validate_params 已拦截，单线程无竞态，这里仅兜底）
        actor_wallet = actor.states.get(INVENTORY_KEY) or {}
        money = actor_wallet.get(MONEY_KEY, 0)
        if give_money > money:
            return [], {"summary": f"金钱不足：你只有 {money}"}
        stock = actor_wallet.get(ITEMS_KEY, {})
        for name, qty in give_items.items():
            have = stock.get(name, 0)
            if qty > have:
                return [], {"summary": f"物品不足：你只有 {name}×{have}"}
        counterparty_wallet = counterparty.states.get(INVENTORY_KEY) or {}
        c_money = counterparty_wallet.get(MONEY_KEY, 0)
        if take_money > c_money:
            return [], {"summary": f"{target} 没有足够的金钱（只有 {c_money}）"}
        c_stock = counterparty_wallet.get(ITEMS_KEY, {})
        for name, qty in take_items.items():
            have = c_stock.get(name, 0)
            if qty > have:
                return [], {"summary": f"{target} 没有 {name}（只有 {name}×{have}）"}

        # 转移双方账目（成功路径才创建钱包字典）
        self._transfer(_wallet(actor.states), _wallet(counterparty.states),
                       give_money, give_items, take_money, take_items)

        detail = self._describe(give_money, give_items, take_money, take_items)
        # 对手方视角：其「付出」= 行动者 take 走的，其「获得」= 行动者 give 的（镜像）
        detail_for_target = self._describe(take_money, take_items, give_money, give_items)

        # 1. 对手方私信（含金额与物品明细，用对手方自己的视角）
        deal_msg = Message(
            sender=agent_name, recipients=[target], target=target,
            content=f"你{detail_for_target}", tag="trade", tick=world.tick,
        )
        world.message_bus.send(deal_msg)
        messages = [deal_msg]

        # 2. 旁观者通知：只列物品，不列金额
        bystanders = world.get_hearable_agents(agent_name, exclude=target)
        if bystanders:
            visible = self._describe_visible(give_items, take_items)
            notice_text = f"与 {target} 进行了一笔交易"
            if visible:
                notice_text += f"（{visible}）"
            notice = Message(
                sender=agent_name, recipients=bystanders, content=notice_text,
                tag="action", tick=world.tick,
            )
            world.message_bus.send(notice)
            messages.append(notice)

        return messages, {"summary": f"交易完成: {detail}"}

    @staticmethod
    def _transfer(actor_wallet: dict, target_wallet: dict,
                  give_money: int, give_items: dict,
                  take_money: int, take_items: dict) -> None:
        """执行双方账目变动：give 从行动者钱包流向对手方，take 反向。"""
        if give_money:
            actor_wallet[MONEY_KEY] = actor_wallet.get(MONEY_KEY, 0) - give_money
            target_wallet[MONEY_KEY] = target_wallet.get(MONEY_KEY, 0) + give_money
        if take_money:
            target_wallet[MONEY_KEY] = target_wallet.get(MONEY_KEY, 0) - take_money
            actor_wallet[MONEY_KEY] = actor_wallet.get(MONEY_KEY, 0) + take_money
        if give_items:
            actor_items = actor_wallet.setdefault(ITEMS_KEY, {})
            target_items = target_wallet.setdefault(ITEMS_KEY, {})
            for name, qty in give_items.items():
                actor_items[name] = actor_items.get(name, 0) - qty
                if actor_items[name] <= 0:
                    del actor_items[name]
                target_items[name] = target_items.get(name, 0) + qty
        if take_items:
            target_items = target_wallet.setdefault(ITEMS_KEY, {})
            actor_items = actor_wallet.setdefault(ITEMS_KEY, {})
            for name, qty in take_items.items():
                target_items[name] = target_items.get(name, 0) - qty
                if target_items[name] <= 0:
                    del target_items[name]
                actor_items[name] = actor_items.get(name, 0) + qty

    @staticmethod
    def _describe(give_money: int, give_items: dict,
                  take_money: int, take_items: dict) -> str:
        """交易明细（发给对手方 / 事件日志 / 行动者记忆）。"""
        parts = []
        if give_money:
            parts.append(f"付出金钱{give_money}")
        if give_items:
            parts.append("付出" + "、".join(f"{n}×{q}" for n, q in give_items.items()))
        if take_money:
            parts.append(f"获得金钱{take_money}")
        if take_items:
            parts.append("获得" + "、".join(f"{n}×{q}" for n, q in take_items.items()))
        return "，".join(parts) or "完成了一次交易"

    @staticmethod
    def _describe_visible(give_items: dict, take_items: dict) -> str:
        """旁观者可见描述：只列涉及的物品名，不列金额与数量。"""
        names = set(give_items) | set(take_items)
        return "、".join(sorted(names))
