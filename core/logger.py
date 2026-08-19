"""日志模块：记录 LLM 调用和系统调试信息。"""
# -*- coding: UTF-8 -*-
from __future__ import annotations

import logging
from pathlib import Path
import json


class SimLogger:
    """模拟日志记录器"""

    def __init__(self, log_file: str = "logs/simulation.log", level: int = logging.INFO):
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("simulation")
        self.logger.setLevel(level)

        self.logger.handlers.clear()

        file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
        file_handler.setLevel(level)

        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)

    def log_llm_call(
        self,
        agent_name: str,
        tick: int,
        mode: str,
        system_prompt: str,
        messages: list[dict],
        schema_or_guide: str,
        raw_response: str,
    ):
        """记录 LLM 调用的完整信息"""
        self.logger.info(
            f"=== LLM CALL: {agent_name} | Tick: {tick} | Mode: {mode} ==="
        )
        self.logger.debug(f"=== SYSTEM PROMPT ===\n{system_prompt}")
        self.logger.debug(f"=== USER MESSAGES ===\n{messages}")
        self.logger.debug(f"=== TOOL SCHEMA / TEXT GUIDE ===\n{schema_or_guide}")
        raw_json = json.loads(raw_response)
        raw_tool_calls = raw_json['choices'][0]['message']['tool_calls']
        for t in raw_tool_calls:
            t['function']['arguments'] = json.loads(t['function']['arguments'])
        self.logger.info(f"=== RAW RESPONSE ===\n{raw_json}")

    def log_tick_start(self, tick: int):
        """记录 tick 开始"""
        self.logger.info(f"=== TICK {tick} START ===")

    def log_tick_end(self, tick: int):
        """记录 tick 结束"""
        self.logger.info(f"=== TICK {tick} END ===")

    def log_agent_action(self, agent_name: str, tick: int, action: dict):
        """记录 agent 执行的 action"""
        self.logger.info(f"ACTION: {agent_name} | Tick: {tick} | {action}")

    def log_message(self, message: dict):
        """记录消息"""
        self.logger.debug(f"MESSAGE: {message}")

    def log_system_event(self, event: str, level: int = logging.INFO):
        """记录系统事件"""
        self.logger.log(level, f"SYSTEM: {event}")

    def debug(self, message: str):
        """记录 DEBUG 级别日志"""
        self.logger.debug(message)

    def info(self, message: str):
        """记录 INFO 级别日志"""
        self.logger.info(message)

    def warning(self, message: str):
        """记录 WARNING 级别日志"""
        self.logger.warning(message)

    def error(self, message: str):
        """记录 ERROR 级别日志"""
        self.logger.error(message)

    def close(self):
        """关闭日志"""
        for handler in self.logger.handlers:
            handler.close()
            self.logger.removeHandler(handler)
