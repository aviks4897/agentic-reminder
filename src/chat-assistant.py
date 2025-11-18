import os
import time
import json
import weave
from agents import set_trace_processors
from weave.integrations.openai_agents.openai_agents import WeaveTracingProcessor

from re import L
from typing import Any, Optional, List, Dict
from agents import set_tracing_export_api_key
set_tracing_export_api_key(os.getenv("OPENAI_API_KEY")) 
from dotenv import load_dotenv

from agents import (
    Agent,
    ItemHelpers,
    ModelSettings,
    RunContextWrapper,
    Runner,
    TResponseInputItem,
    function_tool,
    set_tracing_export_api_key
)
from enum import Enum
from pydantic import BaseModel, Field
import asyncio


weave.init('srinivasan-av-northeastern-university/agent-reminder')
set_trace_processors([WeaveTracingProcessor()])

from prompts import RESPONSE_AGENT_SYSTEM_PROMPT, FEASIBILITY_AGENT_SYSTEM_PROMPT, RESPONSE_AGENT_SYSTEM_PROMPT_V2, INTENT_EXTRACTION_AGENT_SYSTEM_PROMPT
load_dotenv()


def get_openai_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to config.env or your environment before running the agent."
        )
    return api_key



class ConversationStateEnum(str, Enum):
    NEED_WHAT = "NEED_WHAT"
    NEED_WHEN = "NEED_WHEN"
    READY_TO_CHECK = "READY_TO_CHECK"
    NEEDS_FIX = "NEEDS_FIX"
    READY_TO_SCHEDULE = "READY_TO_SCHEDULE"
    DONE = "DONE"


class Slots(BaseModel):
    what: Optional[str] = None
    when: Optional[str] = None
    recurrence: Optional[str] = None
    constraints: List[str] = Field(default_factory=list)
    priority: str = "normal"
    channel: str = "default"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Feasibility(BaseModel):
    last_checked_at: Optional[str] = None
    is_feasible: Optional[bool] = None
    issues: List[str] = Field(default_factory=list)
    alternatives: List[str] = Field(default_factory=list)


class ConversationState(BaseModel):
    state: ConversationStateEnum
    slots: Slots
    feasibility: Feasibility



@function_tool
async def intent_extraction_agent(wrapper: RunContextWrapper[ConversationState], conversation: str):
    """
      Update the full State JSON based on the latest conversation.
    Inputs:
      - wrapper.history: full conversation
      - wrapper.context: current State JSON (keys: state, slots, feasibility)
    Behavior:
      - Extract WHAT/WHEN from recent messages; preserve existing fields unless new info is present.
      - If both WHAT and WHEN exist → set state='READY_TO_CHECK'.
      - If either changes vs prior, reset feasibility.is_feasible=null and last_checked_at=null.
    Returns:
      - The updated State JSON dict only (no prose), matching the schema in the prompt.
    When to call:
      - User provides new or changed info relevant to WHAT/WHEN. 
    
    """

    t0 = time.perf_counter()
    intent_extraction_agent = Agent[Any](
        name = "intent-extraction-agent",
        model="gpt-4.1-mini",
        instructions=INTENT_EXTRACTION_AGENT_SYSTEM_PROMPT
    )

    print("Wrapper: ", wrapper.context)

    print("[TOOL] intent_extraction_agent: called")
    before = wrapper.context.model_dump()
    
    input_payload = json.dumps({
        "Conversation" : conversation,
        "State" : before
    })
    print("[DEBUG] Input Payload: ", before)
    inner_t0 = time.perf_counter()
    raw = await Runner.run(intent_extraction_agent, input=input_payload, context=wrapper.context)
    print(f"[TIME] Runner.run(intent_extraction) {(time.perf_counter()-inner_t0):.3f}s")
    print("[TOOL] INTENT EXTRACTION JSON: ", raw)
    data = json.loads(raw.final_output)
    print("[DATA]: ", data)
    updated = ConversationState(**data)  # validate schema
    out = updated.model_dump()

    print("[DEBUG] UPDATED STATE: ", out)
    # Directly update wrapper.context and log a small diff signal
    # wrapper.context = updated
    w = wrapper

    w.context.slots = updated.slots
    w.context.feasibility = updated.feasibility
    w.context.state = updated.state

    try:
        print("[TOOL] intent_extraction_agent: state ->", out.get("state"))

    except Exception:
        pass
    

    print(f"[TIME] intent_extraction_agent total {(time.perf_counter()-t0):.3f}s")

    return out

@function_tool
async def feasbility_agent(wrapper: RunContextWrapper[ConversationState]) -> Dict[str, Any]:
    """
    Evaluate whether the reminder is feasible and update feasibility fields.
    Inputs:
      - wrapper.context: full State JSON
      - activities/sensors: provided via prompt or inputs (case-insensitive matching)
    Behavior (deterministic):
      - 'Before' reminders are not feasible; specific datetime is generally feasible.
      - If WHAT/WHEN missing or unknown, add a short reason to feasibility.issues.
      - Additionally, if a reminder contains both WHAT/WHEN but is not feasible explain why
      - Set feasibility.is_feasible, issues, alternatives (<=3), last_checked_at (ISO 8601).
      - Optionally set top-level state to 'READY_TO_SCHEDULE' if feasible else 'NEEDS_FIX'.
    Returns:
      - The updated State JSON dict only (no prose), same shape as input schema.
    When to call:
      - After intent extraction sets state ='READY_TO_CHECK'.
    
    """
    t0 = time.perf_counter()

    
    with open("/Users/avikapursrinivasan/agent_reminder_system/src/data/activities.json") as f:
        activities_json = f.read()
    with open("/Users/avikapursrinivasan/agent_reminder_system/src/data/sensors.json") as f:
        sensors_json = f.read()

    agent = Agent[Any](
        name="feasibility-agent",
        model="gpt-5-mini",
        instructions=(FEASIBILITY_AGENT_SYSTEM_PROMPT
        .replace("{activities_json}", activities_json)
        .replace("{sensors_json}", sensors_json)),
        model_settings=ModelSettings(reasoning_effort="minimal")
    )
    print("[TOOL] feasbility_agent: called")

    before = wrapper.context.model_dump()

    input_payload = json.dumps({
        "State" : before
    })
    print("[DEBUG] JSON INPUT FEASIBILITY: ", input_payload)
    raw = await Runner.run(agent, input=input_payload, context=wrapper.context)
    print("[DEBUG] FEASIBILITY OUTPUT: ", raw)

    data = json.loads(raw.final_output)
    updated = ConversationState(**data)  # validate schema
    out = updated.model_dump()

    # Directly update wrapper.context and log a small diff signal

    w = wrapper

    w.context.slots = updated.slots
    w.context.feasibility = updated.feasibility
    w.context.state = updated.state

    print(f"[TIME] feasibility agent total {(time.perf_counter()-t0):.3f}s")
    
    try:
        feas = out.get("feasibility", {})
        print("[TOOL] feasbility_agent: is_feasible ->", feas.get("is_feasible"))
    except Exception:
        pass
    return out



@weave.op
async def handle_turn(user_text: str, history: str, wrapper: RunContextWrapper[ConversationState]) -> Dict[str, Any]:
    # 1) Run chat agent (it may call tools too, but we still do an explicit extraction pass)
    t_0 = time.perf_counter()
    assistant = Agent[Any](
        name="chat-assistant-agent",
        model="gpt-5.1",
        instructions=RESPONSE_AGENT_SYSTEM_PROMPT_V2,
        tools=[intent_extraction_agent, feasbility_agent], 
        # model_settings=ModelSettings(reasoning_effort="minimal") # parent can still call the tool inline if it chooses
    )
    chat_input = json.dumps({
        "user_text": user_text,
        "state": wrapper.context if isinstance(wrapper.context, dict) else wrapper.context.model_dump(),
        "history": history,
    })
    
    # if(wrapper.context.state == ConversationStateEnum.READY_TO_CHECK):
    #     print("[DEBUG] CALLING FEASIBILITY AGENT")
    #     await feasbility_agent(wrapper)
    # Use non-streaming for simplicity here
    assistant_reply = await Runner.run(assistant, input=chat_input, context=wrapper.context)


    print("UPDATED STATE JSON: ", wrapper.context.model_dump())

    print(f"[TIME] Response Agent Total: {(time.perf_counter()-t_0):.3f}s")


    
    return {
        "assistant_reply": assistant_reply.final_output
    }


async def run_chat():
    class ConversationStore:
        """
        Simple in-memory store of conversation turns. Maintains chronological
        user/assistant messages and exposes them as 'history' for agent input.
        """
        def __init__(self) -> None:
            self._messages: List[Dict[str, str]] = []

        def append_user(self, text: str) -> None:
            self._messages.append({"role": "user", "content": text})

        def append_assistant(self, text: str) -> None:
            self._messages.append({"role": "assistant", "content": text})

        def as_history(self) -> List[Dict[str, str]]:
            return list(self._messages)

    # Seed initial conversation state and wrapper
    state = ConversationState(
        state=ConversationStateEnum.NEED_WHAT,
        slots=Slots(),
        feasibility=Feasibility(),
    )
    wrapper = RunContextWrapper[ConversationState](context=state)
    store = ConversationStore()

    print("Type your message. Use /quit to exit.")
    while True:
        try:
            user_text = input("> ").strip()
            if user_text.lower() in {"/q", "/quit", "/exit"}:
                break

            # Record user message and pass full history as context
            store.append_user(user_text)
            
            conversation_str = "\n".join(f"{m['role']}: {m['content']}" for m in store._messages)

            # Let the response agent orchestrate the turn
            result = await handle_turn(user_text, conversation_str, wrapper)
            assistant_reply = result.get("assistant_reply", "")
            print(f"Assistant: {assistant_reply}")


            # Record assistant reply
            store.append_assistant(assistant_reply)
        except (KeyboardInterrupt, EOFError):
            break


if __name__ == "__main__":
    asyncio.run(run_chat())
