"""Action 能力标签常量。

core 会依据这些能力做分支判断，因此用常量避免字符串拼写漂移。
消息类别（Message.tag）不在此列：那只是普通数据标签，不参与 core 判断。
"""

IDLE = "idle"
NPC_CONTROL = "npc_control"
