import asyncio
import importlib.util
import sys
from typing import Any, Dict, List

from agents import RunContextWrapper


_CHAT_ASSISTANT_PATH = "/Users/avikapursrinivasan/agent_reminder_system/src/chat-assistant.py"

_spec = importlib.util.spec_from_file_location("chat_assistant", _CHAT_ASSISTANT_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Unable to load module at {_CHAT_ASSISTANT_PATH}")
chat_assistant = importlib.util.module_from_spec(_spec)
sys.modules["chat_assistant"] = chat_assistant
_spec.loader.exec_module(chat_assistant)


ConversationState = chat_assistant.ConversationState
ConversationStateEnum = chat_assistant.ConversationStateEnum
Slots = chat_assistant.Slots
Feasibility = chat_assistant.Feasibility
handle_turn = chat_assistant.handle_turn


class ConversationStore:
    """
    Simple in-memory store of conversation turns.
    This version is focused on a clean user/assistant transcript for presentation.
    """

    def __init__(self) -> None:
        self._messages: List[Dict[str, str]] = []

    def append_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def append_assistant(self, text: str) -> None:
        self._messages.append({"role": "assistant", "content": text})

    def as_history_str(self) -> str:
        return "\n".join(f"{m['role']}: {m['content']}" for m in self._messages)


async def run_present_chat() -> None:
    """
    Run a cleaner, presentation-focused chat loop that only prints
    user and assistant messages, while still reusing the same agents and
    internal logging defined in chat-assistant.py.
    """
    state = ConversationState(
        state=ConversationStateEnum.NEED_WHAT,
        slots=Slots(),
        feasibility=Feasibility(),
    )
    wrapper: RunContextWrapper[ConversationState] = RunContextWrapper[ConversationState](context=state)
    store = ConversationStore()

    print("Agentic Reminder Assistant (presentation mode). Type /quit to exit.")
    while True:
        try:
            user_text = input("You: ").strip()
            if user_text.lower() in {"/q", "/quit", "/exit"}:
                break

            store.append_user(user_text)
            history_str = store.as_history_str()

            result = await handle_turn(user_text, history_str, wrapper)
            assistant_reply: str = result.get("assistant_reply", "")

            store.append_assistant(assistant_reply)
            print(f"Assistant: {assistant_reply}")

            if "[ChatEnded]" in assistant_reply:
                break
        except (KeyboardInterrupt, EOFError):
            break


if __name__ == "__main__":
    asyncio.run(run_present_chat())


