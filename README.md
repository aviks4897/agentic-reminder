## Agentic Reminder System

### Overview
This project is an **agentic reminder assistant** for elders in a smart home.  
It runs a multi-agent workflow to:

- Talk with the user in natural language
- Incrementally extract the **WHAT** and **WHEN** of a reminder into a structured JSON state
- Check whether the reminder is **feasible** given detectable activities and sensors in the home
- Respond concisely and explain feasibility back to the user

Everything is built around a shared, typed state (`ConversationState`) that flows through the main chat agent and its sub‑agents.

---

## Features

- **Conversation-driven reminder setup**: The chat agent asks short, targeted questions until it has both WHAT and WHEN.
- **Structured state tracking**:
  - Top-level `state` enum: `NEED_WHAT | NEED_WHEN | READY_TO_CHECK | NEEDS_FIX | READY_TO_SCHEDULE | DONE`
  - `slots`: `what`, `when`, `recurrence`, `constraints`, `priority`, `channel`, `metadata`
  - `feasibility`: `last_checked_at`, `is_feasible`, `issues`, `alternatives`
- **Agentic workflow**:
  - `intent_extraction_agent`: keeps the state JSON up to date from the full conversation.
  - `feasbility_agent`: decides whether a reminder is possible given activities/sensors.
  - `chat-assistant-agent`: orchestrator; talks to the user and decides when to call sub‑agents.
- **Stateful conversation loop**: In-memory `ConversationStore` maintains the full user/assistant history and passes it as context every turn.
- **Timing**:
  - Per-call timing logs using `time.perf_counter()` for each agent/tool.
- **Event hooks**:
  - Lightweight event bus (`events.py`) emits events for supervisor turns and tool invocations (intent extraction, feasibility).
  - Streamlit UI subscribes to these events for live visibility.
- **Streamlit UI**:
  - `src/streamlit_app.py` provides a chat-style interface backed by the same agents and state.

---

## Architecture

### Core files

- `chat-assistant.py`
  - Defines:
    - `ConversationStateEnum`, `Slots`, `Feasibility`, `ConversationState`
    - `intent_extraction_agent` (tool)
    - `feasbility_agent` (tool)
    - `handle_turn` (orchestrator function)
    - `run_chat` (CLI loop)
- `prompts.py`
  - Contains all system prompts:
    - `RESPONSE_AGENT_SYSTEM_PROMPT`, `RESPONSE_AGENT_SYSTEM_PROMPT_V2`
    - `INTENT_EXTRACTION_AGENT_SYSTEM_PROMPT`
    - `FEASIBILITY_AGENT_SYSTEM_PROMPT`
- `activities.json`
  - List of detectable activities in the home (used by feasibility).
- `sensors.json`
  - List of detectable sensors in the home (used by feasibility).
- `code-generation.py`
  - Example module that imports `chat-assistant.py` via `importlib` and can call into its APIs (e.g., for codegen or experiments).

### State model

The core state object is `ConversationState`:

- **state** (`ConversationStateEnum`):
  - `NEED_WHAT`, `NEED_WHEN`, `READY_TO_CHECK`, `NEEDS_FIX`, `READY_TO_SCHEDULE`, `DONE`
- **slots** (`Slots`):
  - `what`: string or `None`
  - `when`: string or `None`
  - `recurrence`: string or `None`
  - `constraints`: list of strings
  - `priority`: `"normal"` by default
  - `channel`: `"default"` by default
  - `metadata`: free-form dict
- **feasibility** (`Feasibility`):
  - `last_checked_at`: ISO 8601 string or `None`
  - `is_feasible`: bool or `None`
  - `issues`: list of strings
  - `alternatives`: list of strings

This schema is mirrored both in Pydantic models and in the prompts so the model returns strictly structured JSON.

### Agents & tools

- **Response agent (`chat-assistant-agent`)**
  - Model: `gpt-5.1`
  - Instructions: `RESPONSE_AGENT_SYSTEM_PROMPT_V2`
  - Tools: `[intent_extraction_agent]`
  - Role:
    - Reads current `state` and full `history` string.
    - Decides what to say next.
    - Calls `intent_extraction_agent` when there’s new or changed information about WHAT/WHEN.

- **Intent extraction tool (`intent_extraction_agent`)**
  - Model: `gpt-4.1-mini`
  - Instructions: `INTENT_EXTRACTION_AGENT_SYSTEM_PROMPT`
  - Input payload:
    - `Conversation`: full conversation as a string
    - `State`: current state dict
  - Behavior:
    - Parses the full conversation to update `slots.what` and `slots.when`.
    - Sets `state` to `READY_TO_CHECK` when both WHAT and WHEN are present.
    - Preserves existing fields where there’s no new information.

- **Feasibility tool (`feasbility_agent`)**
  - Model: `gpt-5-mini`
  - Instructions: `FEASIBILITY_AGENT_SYSTEM_PROMPT` with `activities.json` and `sensors.json` injected.
  - Input payload:
    - `state`: full state dict
  - Behavior:
    - Checks if WHAT/WHEN combo is feasible given activities & sensors.
    - Updates `feasibility.is_feasible`, `issues`, `alternatives`, `last_checked_at`.
    - Updates top-level `state` to `READY_TO_SCHEDULE` or `NEEDS_FIX`.

### Orchestration flow (`handle_turn`)

On each user message:

1. The chat agent runs with `user_text`, `history`, and current `state`. It may call `intent_extraction_agent` as a tool to refresh the state.
2. After the chat agent returns, the orchestrator checks `wrapper.context.state`. If it is `READY_TO_CHECK`, it calls `feasbility_agent` to update feasibility and potentially transition to `READY_TO_SCHEDULE` or `NEEDS_FIX`.
3. `handle_turn` returns the assistant reply; the shared `ConversationState` in the wrapper is now updated for the next turn.

The `run_chat` loop maintains history via a simple `ConversationStore`, builds a full conversation string each turn, and passes it into `handle_turn`.

---

## Setup

- Python 3.9+ (dev uses 3.9.6; previously validated on 3.11+)
- Dependencies are pinned in `requirements.txt` (frozen from the current environment).

Create a virtual environment and install dependencies from the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Environment variables:

- `OPENAI_API_KEY` (required). Export it or place it in `.env` so `load_dotenv()` can pick it up.

## Running

- CLI assistant:
  ```bash
  python src/chat-assistant.py
  ```
  Provides a `> ` prompt, returns the assistant reply each turn, and logs timing/state updates. Use `/quit`, `/q`, or `/exit` to leave.

- Streamlit UI:
  ```bash
  streamlit run src/streamlit_app.py
  ```
  Sidebar shows the current `ConversationState` and recent events emitted from supervisor/tool calls.

- Gradio UI:
  ```bash
  python src/gradio_app.py
  ```
  Launches a browser UI that mirrors the same agents, with a live reasoning trail in the accordion panel.

---

## Observability

- Timing: log timestamps and elapsed durations to the console.
- Timing:
  - Tools and the main `handle_turn` function log elapsed time to help you profile latency.

Use the Weave UI to inspect agent/tool invocations, inputs, outputs, and durations.

---

## Example conversation (high-level)

1. User: “I want a reminder to eat dinner.”  
   - State: `NEED_WHEN`, `slots.what = "eat dinner"`.
   - Assistant asks for WHEN.

2. User: “When I enter the kitchen.”  
   - Intent extraction updates `slots.when` and marks `state = READY_TO_CHECK`.
   - Feasibility runs, sees the reminder is not detectable as-is, updates `feasibility.is_feasible = False` and adds an issue.
   - Assistant responds: explains location-based trigger isn’t supported and asks for a time instead.

3. User: “Remind me at 6 pm every day.”  
   - Intent extraction sets `what`, `when`, `recurrence`, `state = READY_TO_CHECK`.
   - Feasibility runs again, now sets `is_feasible = True` and `state = READY_TO_SCHEDULE`.
   - Assistant confirms the reminder and can close with a summary + `[ChatEnded]`.
