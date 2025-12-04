import asyncio
import importlib.util
import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from agents import RunContextWrapper


_CHAT_ASSISTANT_PATH = os.path.join(os.path.dirname(__file__), "chat-assistant.py")
_spec = importlib.util.spec_from_file_location("chat_assistant", _CHAT_ASSISTANT_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Unable to load module at {_CHAT_ASSISTANT_PATH}")

chat_assistant = importlib.util.module_from_spec(_spec)
sys.modules["chat_assistant"] = chat_assistant
_spec.loader.exec_module(chat_assistant)

# Import types from chat_assistant
ChatAssistant = chat_assistant.ChatAssistant
ConversationState = chat_assistant.ConversationState
ConversationStateEnum = chat_assistant.ConversationStateEnum
Slots = chat_assistant.Slots
Feasibility = chat_assistant.Feasibility


app = FastAPI(title="Agentic Reminder Assistant API")
assistant = ChatAssistant()


class HistoryMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    user_text: str
    history: Optional[List[HistoryMessage]] = None
    state: Optional[ConversationState] = None


class ChatResponse(BaseModel):
    assistant_reply: str
    state: ConversationState
    history: List[HistoryMessage]


def _history_to_string(messages: List[HistoryMessage]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """
    Single-turn chat endpoint that:
    - Takes user_text, prior history, and current ConversationState
    - Calls the ChatAssistant.handle_turn agent
    - Returns the assistant reply and updated state + history
    """
    # Seed state if caller did not provide one
    state = req.state or ConversationState(
        state=ConversationStateEnum.NEED_WHAT,
        slots=Slots(),
        feasibility=Feasibility(),
    )
    wrapper: RunContextWrapper[ConversationState] = RunContextWrapper(context=state)

    history: List[HistoryMessage] = req.history or []
    history_str = _history_to_string(history)

    result: Dict[str, Any] = await assistant.handle_turn(
        user_text=req.user_text,
        history=history_str,
        wrapper=wrapper,
    )
    assistant_reply = result.get("assistant_reply", "")

    # Append assistant reply to history for the caller
    new_history = history + [HistoryMessage(role="assistant", content=assistant_reply)]

    return ChatResponse(
        assistant_reply=assistant_reply,
        state=wrapper.context,
        history=new_history,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


