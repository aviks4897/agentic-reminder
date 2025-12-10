import importlib.util
import os
import sys
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI
from pydantic import BaseModel

from agents import RunContextWrapper
from code_generation import CodeGeneration
from json_converter import generate_json


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
    generated_yaml: Optional[str] = None


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
    trigger_json_str: Optional[str] = None

    # If the conversation has ended, trigger code + JSON → YAML generation once and attach result
    if "[ChatEnded]" in assistant_reply:
        try:
            state_dict = (
                wrapper.context
                if isinstance(wrapper.context, dict)
                else wrapper.context.model_dump()
            )
            # 1) Generate trigger/cancel code from the final state
            code_obj = await CodeGeneration.generate_code(state_dict)
            combined_code = (
                (code_obj.get("generated_trigger_code") or "")
                + "\n\n"
                + (code_obj.get("generated_cancel_code") or "")
            )

            # 2) Use json_converter agent to build a TriggerMachine JSON string
            trigger_json_str = generate_json(combined_code)
        except Exception as e:
            trigger_json_str = f"# code_generation_failed: {e}"

    # Append assistant reply to history for the caller
    new_history = history + [HistoryMessage(role="assistant", content=assistant_reply)]

    return ChatResponse(
        assistant_reply=assistant_reply,
        state=wrapper.context,
        history=new_history,
        generated_json=trigger_json_str,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


