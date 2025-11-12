from ast import main
import os
import time
import json
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



load_dotenv()


def get_openai_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to config.env or your environment before running the agent."
        )
    return api_key


RESPONSE_AGENT_SYSTEM_PROMPT="""
You are a helpful chat bot designed to schedule user reminders for elders in a smart-home. Ask short, clarifying questions to understand the user's preferences.

INPUTS:
    • Current User Message
    • Previous User Chats
    • Current State JSON object

YOUR GOALS:

    1. **Do not stop the conversation until you obtain the following conditions**
        a. The WHAT of the reminder. *The activity or event that triggers the reminder*
        b. The WHEN of the reminder. *The time or period it should take place in*

    2. **Call necessary sub-agents when there is an update to the state**
        a. Call the 'intent_extraction_agent' to update the state object JSON when new pieces of detail have been added to the conversation.
            i. Pass in the full history string of the conversation provided under 'history'
        b. When the state == 'READY_TO_CHECK' call the 'feasibility_agent'

    3. **Respond as concisely as possible**:
        a. Do not overexplain and detract from the goal of obtaining the reminder conditions
        b. Follow the current conversation details as closely as possible, prefer more recent messages in the instance user reminder preferences change

    4. **Process input from feasibility bot and state JSON appropriately**
        a. When given an update about whether a reminder is feasibile or not follow it appropriately and do NOT ignore.
            i. If a reminder is not feasible inform the user and continue conversation
        b. Use the 'state' given to correctly structure the next question asked.
        c. If reminder is not feasible, parse 'issues' to determine why and provide user with exact reasoning 
    
    5. **Be concise and methodical with your responses**
        a. Self-reflect "How can I get information I can add to the state object?"
    
    6. **Finish Conversations Appropriately**
        a. When the state contains both the what/when and 'feasibility' is set to True, end conversation with a summary of the reminder + [ChatEnded]
            i. "I'll remind you to take your dog for a walk at 5 p.m. [ChatEnded]

You have autonomy on when to continue and terminate the conversation. Ask strategic questions that help you get important details for scheduling a real reminder.

"""


INTENT_EXTRACTION_AGENT_SYSTEM_PROMPT="""

You are a intent extractor agent in charge of maintaining a JSON object that keeps track of the current state of a conversation between a reminder assistant agent and a user.

Your main responsibilities cover keep tracking of the WHAT and the WHEN of the reminder conversation intermittently between the user and assistant chats.

INPUT
 • Full user-agent conversation.
 • State JSON Object



GUIDELINES FOR EXTRACTION:

    1.**Focus on the intent of the user**
        a. This includes the WHAT and the WHEN of the reminder conversation

    2. **Adhere to the schema and update with relevant information**
        a. Update with WHAT or WHEN if relevant in conversation chat
        b. Update state based on next relevant step to check
            i. Use best judgement of listed catgories under 'state' key to determine what should be obtained next
            ii. When both the 'what' and 'when' are provided make state as 'READY_TO_CHECK'


JSON Object Schema:

    {
    "state": "NEED_WHAT | NEED_WHEN | READY_TO_CHECK | NEEDS_FIX | READY_TO_SCHEDULE | DONE",
    "slots": {
        "what": null,
        "when": null,
        "recurrence": null,
        "constraints": [],
        "priority": "normal",
        "channel": "default",
        "metadata": {}
    },
    "feasibility": {
        "last_checked_at": null,
        "is_feasible": null,
        "issues": [],
        "alternatives": []
    }
    }

Return ONLY the updated state JSON.
"""

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


FEASIBILITY_AGENT_SYSTEM_PROMPT="""
You are a high-reasoning feasibility agent designed to determine whether a reminder is possible or not.

INPUT:
    • State JSON Object
    • List of detectable activities: {activities_json}
    • List of detectable sensors: {sensors_json}

GOALS
    1. **Parse list of activites and sensors and determine if reminder is feasible**
        a. If activity or sensor in State JSON doesn't match list, it is not feasible. Parse JSON 'what' and 'when' keys to get a good grasp of what the reminder is about.
        b. "Before" Activity reminders are never feasible, only during the activity or after times are possible

    2. **Check time-based activities**
        a. If the WHEN is a specific datetime the reminder is usually feasible
        b. Check recurrence patterns to determine if reminder is possible. The minimum recurrence period is daily.

    3. **Follow WHEN Constraints**
        a. Reminders are not feasible if they are not detectable by either a single or combination of sensors, activities or specific clock times.
    
    4. **Update State JSON Object**
        a. When you come to a conclusion about the feasibility of a reminder, update the JSON state object 
        b. Describe your thought process and reasoning when determining if a reminder is feasible or not. Give a short blurb, less than a sentence, explaning why it is not feasible in the 'issues' section.
    
    5. **If State JSON doesn't have enough information, update state accordingly**
        a. Update 'state' key accordingly if there isn't enough information to make a feasibility claim
        b. If either 'what' or 'when' is null, reject respond and provide 'not enough information' in 'issues'
    


Return the updated state JSON.

"""


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

    # t0 = time.perf_counter()
    intent_extraction_agent = Agent[Any](
        name = "intent-extraction-agent",
        model="gpt-5",
        instructions=INTENT_EXTRACTION_AGENT_SYSTEM_PROMPT,
        model_settings=ModelSettings(reasoning_effort="minimal")
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
    

    # print(f"[TIME] intent_extraction_agent total {(time.perf_counter()-t0):.3f}s")

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
      - After intent extraction sets state='READY_TO_CHECK'.
    
    """


    
    with open("/Users/avikapursrinivasan/agent_reminder_system/src/activities.json") as f:
        activities_json = f.read()
    with open("/Users/avikapursrinivasan/agent_reminder_system/src/sensors.json") as f:
        sensors_json = f.read()

    agent = Agent[Any](
        name="feasibility-agent",
        model="gpt-5",
        instructions=FEASIBILITY_AGENT_SYSTEM_PROMPT.format(
            activities_json=activities_json,
            sensors_json=sensors_json,
        ),
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
    
    try:
        feas = out.get("feasibility", {})
        print("[TOOL] feasbility_agent: is_feasible ->", feas.get("is_feasible"))
    except Exception:
        pass
    return out




async def handle_turn(user_text: str, history: str, wrapper: RunContextWrapper[ConversationState]) -> Dict[str, Any]:
    # 1) Run chat agent (it may call tools too, but we still do an explicit extraction pass)
    # t_0 = time.perf_counter()
    assistant = Agent[Any](
        name="chat-assistant-agent",
        model="gpt-5",
        instructions=RESPONSE_AGENT_SYSTEM_PROMPT,
        tools=[intent_extraction_agent, feasbility_agent], 
        model_settings=ModelSettings(reasoning_effort="minimal") # parent can still call the tool inline if it chooses
    )
    chat_input = json.dumps({
        "user_text": user_text,
        "state": wrapper.context if isinstance(wrapper.context, dict) else wrapper.context.model_dump(),
        "history": history,
    })
    
    # Use non-streaming for simplicity here
    assistant_reply = await Runner.run(assistant, input=chat_input, context=wrapper.context)


    print("UPDATED STATE JSON: ", wrapper.context.model_dump())

    # print(f"[TIME] Response Agent Total: {(time.perf_counter()-t_0):.3f}s")
    
    return {
        "assistant_reply": assistant_reply,
        "state": wrapper.context,
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
