
import importlib.util
import os
import sys
from typing import Any

from agents import RunContextWrapper
from dotenv import load_dotenv

_CHAT_ASSISTANT_PATH = os.path.join(os.path.dirname(__file__), "chat-assistant.py")

_spec = importlib.util.spec_from_file_location("chat_assistant", _CHAT_ASSISTANT_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Unable to load module at {_CHAT_ASSISTANT_PATH}")
chat_assistant = importlib.util.module_from_spec(_spec)
sys.modules["chat_assistant"] = chat_assistant
_spec.loader.exec_module(chat_assistant)

ConversationState = chat_assistant.ConversationState
ConversationStateEnum = chat_assistant.ConversationStateEnum
handle_turn = chat_assistant.handle_turn

load_dotenv()


async def generate_code(state: dict) -> Any:
    ...
    
