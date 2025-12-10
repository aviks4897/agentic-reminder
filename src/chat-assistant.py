import os
import time
import json
import weave
import sqlite3
from datetime import datetime, timezone
import uuid
from agents import set_trace_processors
from code_generation import CodeGeneration
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

from prompts import (
    RESPONSE_AGENT_SYSTEM_PROMPT, 
    FEASIBILITY_AGENT_SYSTEM_PROMPT, 
    RESPONSE_AGENT_SYSTEM_PROMPT_V2, 
    INTENT_EXTRACTION_AGENT_SYSTEM_PROMPT, 
    FEASIBILITY_AGENT_SYSTEM_PROMPT_V2, 
    INTENT_EXTRACTION_AGENT_SYSTEM_PROMPT_V2
)
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
    when: Optional[When] = None
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


class When(BaseModel):
    inferred_time: Optional[str] = None
    exact_time : ExactTime

class ExactTime(BaseModel):
    start_time : Optional[str] = None
    end_time : Optional[str] = None

class ChatAssistant:
    with open("/Users/avikapursrinivasan/agent_reminder_system/src/data/activities.json") as f:
        activities_json = f.read()
    with open("/Users/avikapursrinivasan/agent_reminder_system/src/data/sensors.json") as f:
        sensors_json = f.read()

    with open("/Users/avikapursrinivasan/agent_reminder_system/src/data/detectable_events.json") as f:
        DETECTABLE_ACTIVITIES = f.read()

    with open("/Users/avikapursrinivasan/agent_reminder_system/src/data/user_prefs.json") as f:
        USER_PREFERENCES = f.read()

    DB_PATH: str = "/Users/avikapursrinivasan/agent_reminder_system/conversations.db"


    def __init__(
        self, 
        detectable_activities: str = DETECTABLE_ACTIVITIES, 
        db_path: str = DB_PATH, 
        user_prefs: str = USER_PREFERENCES, 
        activities_json: str = activities_json,
        sensors_json: str = sensors_json
     ) -> None:
        
        self.DB_PATH = db_path
        self.DETECTABLE_ACTIVITIES = detectable_activities
        self.activities_json = activities_json
        self.sensors_json = sensors_json


    def init_db(self) -> None:
        """
        Ensure the SQLite database and 'conversations' table exist.
        """
        conn = sqlite3.connect(self.DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                ended_at TEXT NOT NULL,
                conversation_str TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()


    def save_conversation_to_db(self, conversation_str: str) -> None:
        """
        Append a full conversation transcript to the SQLite database.
        """
        print(f"[DB] Saving conversation of length {len(conversation_str)} to {self.DB_PATH}")
        conn = sqlite3.connect(self.DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO conversations (id, ended_at, conversation_str) VALUES (?, ?, ?)",
            (
                str(uuid.uuid4()),
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                conversation_str
            ),
        )
        conn.commit()
        conn.close()
    
    @function_tool
    async def intent_extraction_agent(wrapper: RunContextWrapper[ConversationState], conversation: str):
        """
        Update the full State JSON based on the latest conversation.
        Inputs:
        - wrapper.context: current State JSON (keys: state, slots, feasibility)
        - conversation: full user-agent conversation string
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
            model="gpt-5.1",
            instructions=INTENT_EXTRACTION_AGENT_SYSTEM_PROMPT_V2
        )

        print("Wrapper: ", wrapper.context)

        print("[TOOL] intent_extraction_agent: called")
        before = wrapper.context.model_dump()
        
        input_payload = json.dumps({
            "Conversation" : conversation,
            "State" : before
        })
        print("[DEBUG] Input Payload: ", input_payload)
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

        SYSTEM_PROMPT = (
            FEASIBILITY_AGENT_SYSTEM_PROMPT_V2
            .replace("{user_prefs}", json.dumps(ChatAssistant.USER_PREFERENCES))
            .replace("{detectable_events}", json.dumps(ChatAssistant.DETECTABLE_ACTIVITIES))
        )
        print("[DEBUG] FEASIBILITY SYSTEM PROMPT: ", SYSTEM_PROMPT)


        agent = Agent[Any](
            name="feasibility-agent",
            model="gpt-5.1",
            instructions=FEASIBILITY_AGENT_SYSTEM_PROMPT_V2
            .replace("{user_prefs}", json.dumps(ChatAssistant.USER_PREFERENCES))
            .replace("{detectable_events}", json.dumps(ChatAssistant.DETECTABLE_ACTIVITIES))
            ,
            model_settings=ModelSettings(reasoning_effort="high")
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
    async def handle_turn(self, user_text: str, history: str, wrapper: RunContextWrapper[ConversationState]) -> Dict[str, Any]:
        # 1) Run chat agent (it may call tools too, but we still do an explicit extraction pass)
        t_0 = time.perf_counter()
        current_time = datetime.now().isoformat()
        assistant = Agent[Any](
            name="chat-assistant-agent",
            model="gpt-5.1",
            instructions=RESPONSE_AGENT_SYSTEM_PROMPT_V2.replace("{current_time}", current_time),
            tools=[self.intent_extraction_agent, self.feasbility_agent], 
            # model_settings=ModelSettings(reasoning_effort="minimal") # parent can still call the tool inline if it chooses
        )
        chat_input = json.dumps({
            "user_text": user_text,
            "state": wrapper.context if isinstance(wrapper.context, dict) else wrapper.context.model_dump(),
            "history": history
        })
        
        assistant_reply = await Runner.run(assistant, input=chat_input, context=wrapper.context)


        print("UPDATED STATE JSON: ", wrapper.context.model_dump())

        print(f"[TIME] Response Agent Total: {(time.perf_counter()-t_0):.3f}s")


        
        return {
            "assistant_reply": assistant_reply.final_output
        }


    async def run_chat(self):
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

        # Ensure DB/table exists before starting the chat loop
        self.init_db()

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
                result = await self.handle_turn(user_text, conversation_str, wrapper)
                assistant_reply = result.get("assistant_reply", "")
                print(f"Assistant: {assistant_reply}")

                store.append_assistant(assistant_reply)
                print("[DEBUG] Finished Appending Assistant Reply")

                # When the chat signals completion, persist conversation and trigger code generation
                if "[ChatEnded]" in assistant_reply:
                    print("[CHAT] Conversation ended, saving transcript and generating code.")
                    final_message = f"\nassistant: {assistant_reply}"
                    final_str = conversation_str + final_message
                    print(f"[DB] Final conversation length before save: {len(final_str)}")
                    self.save_conversation_to_db(conversation_str=final_str)

                    # Kick off code generation based on the final state
                    try:
                        state_dict = (
                            wrapper.context
                            if isinstance(wrapper.context, dict)
                            else wrapper.context.model_dump()
                        )
                        generated_code = await CodeGeneration.generate_code(state_dict)
                        
                        print("\n[CODEGEN] Generated reminder function:\n")
                        print(generated_code)
                    except Exception as e:
                        print(f"[CODEGEN] Error generating code: {e}")

                    break
            except (KeyboardInterrupt, EOFError):
                break


