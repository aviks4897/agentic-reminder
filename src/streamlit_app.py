import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Dict, List

import streamlit as st
import streamlit.components.v1 as components
from agents.run_context import RunContextWrapper
from dotenv import load_dotenv

from events import EventRecord, event_bus

load_dotenv()


def _load_chat_assistant_module():
    base_dir = Path(__file__).resolve().parent
    module_path = base_dir / "chat-assistant.py"
    spec = importlib.util.spec_from_file_location("chat_assistant", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module at {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


chat_assistant = _load_chat_assistant_module()
ConversationState = chat_assistant.ConversationState
ConversationStateEnum = chat_assistant.ConversationStateEnum
Feasibility = chat_assistant.Feasibility
Slots = chat_assistant.Slots
handle_turn = chat_assistant.handle_turn


def _init_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages: List[Dict[str, str]] = []
    if "event_log" not in st.session_state:
        st.session_state.event_log: List[EventRecord] = []
    if "is_thinking" not in st.session_state:
        st.session_state.is_thinking = False
    if "pending_prompt" not in st.session_state:
        st.session_state.pending_prompt = None
    if "last_turn_events" not in st.session_state:
        st.session_state.last_turn_events: List[EventRecord] = []
    if "last_viewed_event_idx" not in st.session_state:
        st.session_state.last_viewed_event_idx = 0
    if "wrapper" not in st.session_state:
        state = ConversationState(
            state=ConversationStateEnum.NEED_WHAT,
            slots=Slots(),
            feasibility=Feasibility(),
        )
        st.session_state.wrapper = RunContextWrapper[ConversationState](context=state)
    if "chat_closed" not in st.session_state:
        st.session_state.chat_closed = False
    if "event_listener_registered" not in st.session_state:
        def _listener(record: EventRecord) -> None:
            st.session_state.event_log.append(record)

        st.session_state._unsubscribe_events = event_bus.subscribe(_listener)
        st.session_state.event_listener_registered = True


def _history_str(messages: List[Dict[str, str]]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in messages)


def _render_sidebar() -> None:
    st.sidebar.header("Conversation State")
    st.sidebar.json(st.session_state.wrapper.context.model_dump())

    st.sidebar.header("Recent Events")
    if not st.session_state.event_log:
        st.sidebar.info("Events will appear here as the assistant works.")
        return

    # Show the 15 most recent events to avoid overwhelming the UI.
    for record in list(st.session_state.event_log)[-15:][::-1]:
        with st.sidebar.expander(f"{record.timestamp} — {record.name}", expanded=False):
            st.code(json.dumps(record.payload, indent=2))


def _render_timeline(events: List[EventRecord]) -> None:
    if not events:
        return

    def _label(event_name: str) -> str:
        if "supervisor" in event_name:
            return "SUPERVISOR"
        if "tool" in event_name:
            return "TOOL"
        return "EVENT"

    def _title(event: EventRecord) -> str:
        name = event.name
        tool = event.payload.get("tool")
        if name == "supervisor_evaluating_conversation":
            return "Supervisor evaluating turn"
        if name == "supervisor_completed_turn":
            return "Supervisor reply finalized"
        if name == "tool_started" and tool:
            if "intent" in tool:
                return "Intent extraction started"
            if "feas" in tool:
                return "Feasibility check started"
            return f"Tool started: {tool}"
        if name == "tool_completed" and tool:
            if "intent" in tool:
                return "Intent extraction completed"
            if "feas" in tool:
                return "Feasibility check completed"
            return f"Tool completed: {tool}"
        return name.replace("_", " ").title()

    def _short(text: str, n: int = 80) -> str:
        return text if len(text) <= n else text[:n] + "…"

    def _detail(event: EventRecord) -> str:
        name = event.name
        payload = event.payload
        state = payload.get("state") or payload.get("state_after") or payload.get("state_before")
        slots = (state or {}).get("slots", {}) if isinstance(state, dict) else {}
        feasibility = (state or {}).get("feasibility", {}) if isinstance(state, dict) else {}
        conv_state = (state or {}).get("state") if isinstance(state, dict) else None

        if name == "supervisor_evaluating_conversation":
            user = payload.get("user_text", "")
            if not slots.get("what") and not slots.get("when"):
                slot_summary = "no WHAT/WHEN yet"
            else:
                parts = []
                if slots.get("what"):
                    parts.append(f'what="{_short(str(slots["what"]), 32)}"')
                if slots.get("when"):
                    parts.append(f'when="{_short(str(slots["when"]), 32)}"')
                slot_summary = ", ".join(parts) if parts else "no updated slots"
            return f'User said "{_short(str(user), 40)}"; state={conv_state or "—"}, {slot_summary}.'

        if name == "supervisor_completed_turn":
            reply = payload.get("assistant_reply", "")
            feas = feasibility.get("is_feasible")
            if feas is True:
                feas_str = "feasible"
            elif feas is False:
                feas_str = "not feasible"
            else:
                feas_str = "feasibility not checked"

            state_phrase = {
                "NEED_WHAT": "asking for WHAT details",
                "NEED_WHEN": "asking for WHEN details",
                "READY_TO_CHECK": "ready to run feasibility",
                "NEEDS_FIX": "asking user to fix invalid details",
                "READY_TO_SCHEDULE": "confirming a feasible reminder",
                "DONE": "closing the reminder setup",
            }.get(conv_state or "", f"state={conv_state or '—'}")

            return f'{state_phrase}; reply "{_short(str(reply), 48)}"; {feas_str}.'

        tool = payload.get("tool")
        if name == "tool_started" and tool:
            if "intent" in tool:
                return "Start intent extraction from full conversation."
            if "feas" in tool:
                return "Start feasibility reasoning over current state."
            return f"Running tool {tool}."

        if name == "tool_completed" and tool:
            if "intent" in tool:
                what = slots.get("what")
                when = slots.get("when")
                slot_summary = []
                if what:
                    slot_summary.append(f'what="{_short(str(what), 32)}"')
                if when:
                    slot_summary.append(f'when="{_short(str(when), 32)}"')
                joined = ", ".join(slot_summary) if slot_summary else "no slot changes"
                return f"Intent updated → {joined}."
            if "feas" in tool:
                feas = feasibility.get("is_feasible")
                issues = feasibility.get("issues") or []
                feas_str = "unknown"
                if feas is True:
                    feas_str = "feasible"
                elif feas is False:
                    feas_str = "not feasible"
                issue = _short(issues[0], 60) if issues else ""
                return f"Feasibility={feas_str}" + (f" — {issue}" if issue else "")
            return f"Tool {tool} finished."

        # Fallbacks
        if "assistant_reply" in payload:
            return f'Reply "{_short(str(payload.get("assistant_reply")), 60)}".'
        if "user_text" in payload:
            return f'User "{_short(str(payload.get("user_text")), 60)}".'

        return "Internal step."

    steps_html = ""
    for event in events:
        label = _label(event.name)
        title = _title(event)
        detail = _detail(event)
        steps_html += f"""
        <div class="tl-item">
            <div class="tl-dot"></div>
            <div class="tl-body">
                <div class="tl-header">
                    <span class="tl-label">{label}</span>
                    <span class="tl-title">{title}</span>
                </div>
                <div class="tl-detail">{detail}</div>
            </div>
        </div>
        """

    html = f"""
    <style>
    .tl-wrap {{
        max-width: 640px;
        margin: 4px 0;
    }}
    .tl {{
        position: relative;
        padding-left: 14px;
    }}
    .tl::before {{
        content: "";
        position: absolute;
        top: 4px;
        left: 6px;
        width: 2px;
        height: 100%;
        background: #e5e7eb;
    }}
    .tl-item {{
        position: relative;
        display: grid;
        grid-template-columns: 14px 1fr;
        gap: 10px;
        margin-bottom: 14px;
    }}
    .tl-dot {{
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: #2563eb;
        margin-top: 6px;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.16);
        z-index: 1;
    }}
    .tl-body {{
        padding-left: 6px;
    }}
    .tl-header {{
        display: flex;
        gap: 8px;
        align-items: baseline;
    }}
    .tl-label {{
        font-size: 12px;
        font-weight: 700;
        color: #1d4ed8;
        text-transform: uppercase;
        letter-spacing: 0.01em;
    }}
    .tl-title {{
        font-size: 14px;
        font-weight: 700;
        color: #111827;
    }}
    .tl-detail {{
        font-size: 13px;
        color: #4b5563;
        margin: 4px 0;
    }}
    </style>
    <div class="tl-wrap">
        <div class="tl">
            {steps_html}
        </div>
    </div>
    """
    components.html(html, height=max(120, min(420, 120 * len(events))), scrolling=True)


def _render_reasoning_animation() -> None:
    if not st.session_state.is_thinking:
        return
    events = list(st.session_state.event_log)[-8:]
    _render_timeline(events)


def _render_live_thinking(prompt: str) -> None:
    preview = prompt[:220] + ("…" if len(prompt) > 220 else "")
    html = f"""
    <style>
    .live-think {{
        border: 1px solid #e0e7ff;
        background: linear-gradient(135deg, #f0f4ff, #ffffff);
        border-radius: 12px;
        padding: 12px 14px;
        box-shadow: 0 6px 16px rgba(0,0,0,0.05);
    }}
    .live-think-title {{
        font-weight: 700;
        color: #0f172a;
        margin-bottom: 6px;
    }}
    .live-think-dots {{
        display: inline-flex;
        gap: 6px;
        margin-left: 6px;
    }}
    .live-think-dots span {{
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #2563eb;
        animation: bounce 1.4s infinite ease-in-out both;
    }}
    .live-think-dots span:nth-child(1) {{ animation-delay: -0.32s; }}
    .live-think-dots span:nth-child(2) {{ animation-delay: -0.16s; }}
    .live-think-body {{
        font-size: 13px;
        color: #1f2937;
        margin: 6px 0 0 0;
    }}
    @keyframes bounce {{
        0%, 80%, 100% {{ transform: scale(0); opacity: 0.4; }}
        40% {{ transform: scale(1); opacity: 1; }}
    }}
    </style>
    <div class="live-think">
        <div class="live-think-title">Assistant is thinking<div class="live-think-dots"><span></span><span></span><span></span></div></div>
        <div class="live-think-body">Processing your message: "{preview}"</div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def main() -> None:
    st.set_page_config(page_title="Agentic Reminder Assistant", page_icon="⏰")
    _init_session_state()
    _render_sidebar()

    st.title("Agentic Reminder Assistant")
    st.caption("Streamlit UI built on the existing agent workflow.")

    for idx, message in enumerate(st.session_state.messages):
        if message["role"] == "assistant" and idx == len(st.session_state.messages) - 1 and st.session_state.last_turn_events:
            with st.expander("Reasoning for last turn", expanded=False):
                _render_timeline(st.session_state.last_turn_events)
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if st.session_state.is_thinking:
        with st.container():
            if st.session_state.pending_prompt:
                st.markdown(f"**Thinking about:** {st.session_state.pending_prompt}")
            _render_reasoning_animation()

    prompt = st.chat_input(
        "Type your reminder request",
        disabled=st.session_state.chat_closed,
    )

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        history = _history_str(st.session_state.messages)
        st.session_state.is_thinking = True
        st.session_state.pending_prompt = prompt
        prev_events_len = len(st.session_state.event_log)
        with st.status("Assistant is thinking...", expanded=True) as status:
            _render_live_thinking(prompt)
            result = asyncio.run(handle_turn(prompt, history, st.session_state.wrapper))
            status.update(label="Reply ready", state="complete")
        st.session_state.last_turn_events = st.session_state.event_log[prev_events_len:]
        assistant_reply: str = result.get("assistant_reply", "")
        st.session_state.messages.append({"role": "assistant", "content": assistant_reply})
        st.session_state.is_thinking = False
        st.session_state.pending_prompt = None
        if "[ChatEnded]" in assistant_reply:
            st.session_state.chat_closed = True
        st.rerun()


if __name__ == "__main__":
    main()
