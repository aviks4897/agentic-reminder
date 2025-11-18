

import importlib.util
import sys
from typing import Any
from agents import RunContextWrapper

# Load chat-assistant.py (hyphenated filename) as a module named 'chat_assistant'
_CHAT_ASSISTANT_PATH = "/Users/avikapursrinivasan/agent_reminder_system/src/chat-assistant.py"

_spec = importlib.util.spec_from_file_location("chat_assistant", _CHAT_ASSISTANT_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Unable to load module at {_CHAT_ASSISTANT_PATH}")
chat_assistant = importlib.util.module_from_spec(_spec)
sys.modules["chat_assistant"] = chat_assistant
_spec.loader.exec_module(chat_assistant)

# Re-export commonly used symbols for type hints and calls

ConversationState = chat_assistant.ConversationState
ConversationStateEnum = chat_assistant.ConversationStateEnum
handle_turn = chat_assistant.handle_turn



async def generate_code(state: dict) -> Any:
    

    