import asyncio
import importlib.util
import sys

_CHAT_ASSISTANT_PATH = "/Users/avikapursrinivasan/agent_reminder_system/src/chat-assistant.py"

_spec = importlib.util.spec_from_file_location("chat_assistant", _CHAT_ASSISTANT_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Unable to load module at {_CHAT_ASSISTANT_PATH}")

chat_assistant = importlib.util.module_from_spec(_spec)
sys.modules["chat_assistant"] = chat_assistant
_spec.loader.exec_module(chat_assistant)

ChatAssistant = chat_assistant.ChatAssistant


async def main() -> None:
    """
    Simple REPL to manually test the ChatAssistant.
    """
    assistant = ChatAssistant()
    await assistant.run_chat()


if __name__ == "__main__":
    asyncio.run(main())





