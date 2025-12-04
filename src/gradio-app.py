import asyncio
import importlib.util
import json
import os
import sys
from typing import Dict, List

import gradio as gr

from agents.run_context import RunContextWrapper
from events import EventRecord, event_bus
from code_generation import CodeGeneration


_CHAT_ASSISTANT_PATH = os.path.join(os.path.dirname(__file__), "chat-assistant.py")
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
ChatAssistant = chat_assistant.ChatAssistant


def _new_conversation_state() -> ConversationState:
    return ConversationState(
        state=ConversationStateEnum.NEED_WHAT,
        slots=Slots(),
        feasibility=Feasibility(),
    )


# Single shared conversation state for this app instance.
_initial_state = _new_conversation_state()
_wrapper = RunContextWrapper[ConversationState](context=_initial_state)
_assistant = ChatAssistant()


CUSTOM_CSS = """
/* Layout tweaks */
.gradio-container {{
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
}}

#agent-header {{
  text-align: center;
  margin-top: 0.75rem;
  margin-bottom: 0.25rem;
}}

#agent-subtitle {{
  text-align: center;
  color: #4b5563;
  margin-bottom: 0.75rem;
}}

/* Hide default Gradio footer + branding links */
footer, .svelte-1ipelgc, a[href*="gradio.app"] {{
  display: none !important;
}}

.gr-accordion {{
  border: 1px solid #e5e7eb !important;
  border-radius: 0.85rem !important;
  background: #fff;
}}

.gr-accordion > summary {{
  font-weight: 600;
  font-size: 0.92rem;
  color: #111827;
}}

#reasoning-panel {{
  border-radius: 0.75rem;
  background: #fbfcff;
  border: 1px solid #e5e7eb;
  padding: 0.6rem 0.9rem;
  font-size: 0.85rem;
  position: relative;
  overflow: hidden;
}}

#reasoning-panel::before {{
  content: "";
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at top left, rgba(37, 99, 235, 0.08), transparent 45%);
  pointer-events: none;
}}

.reasoning-subtitle {{
  color: #6b7280;
  font-size: 0.78rem;
  margin-bottom: 0.3rem;
}}

.reasoning-steps {{
  margin-top: 0.35rem;
  position: relative;
  padding-left: 1.2rem;
}}

.reasoning-steps::before {{
  content: "";
  position: absolute;
  top: 0.4rem;
  bottom: 0.4rem;
  left: 0.4rem;
  width: 2px;
  background: rgba(37, 99, 235, 0.2);
}}

.reason-step {{
  position: relative;
  margin-bottom: 0.6rem;
  padding-left: 0.6rem;
}}

.reason-step-dot {{
  position: absolute;
  left: -0.9rem;
  top: 0.2rem;
  width: 1.2rem;
  height: 1.2rem;
  border-radius: 999px;
  background: #ffffff;
  border: 2px solid #2563eb;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  color: #2563eb;
  box-shadow: 0 2px 6px rgba(37, 99, 235, 0.25);
}}

.reason-step-title {{
  font-weight: 600;
  color: #0f172a;
  font-size: 0.82rem;
}}

.reason-step-text {{
  font-size: 0.78rem;
  color: #475569;
  margin-top: 0.15rem;
}}

.reasoning-empty {{
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: #94a3b8;
}}

.reasoning-empty-icon {{
  width: 1.8rem;
  height: 1.8rem;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.12);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: float 2s ease-in-out infinite;
}}

.reasoning-empty-message {{
  font-size: 0.8rem;
}}

@keyframes float {{
  0%, 100% {{ transform: translateY(0); }}
  50% {{ transform: translateY(-4px); }}
}}
"""


def _short(text: str, n: int = 80) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _reasoning_title(event: EventRecord) -> str:
    name = event.name
    tool = event.payload.get("tool")
    state = event.payload.get("state") or event.payload.get("state_after") or event.payload.get("state_before")
    conv_state = (state or {}).get("state") if isinstance(state, dict) else None

    if name == "supervisor_evaluating_conversation":
        return "Supervisor: understanding your request"
    if name == "supervisor_completed_turn":
        if conv_state == "READY_TO_CHECK":
            return "Supervisor: ready to check feasibility"
        if conv_state == "READY_TO_SCHEDULE":
            return "Supervisor: confirming a feasible plan"
        if conv_state == "NEEDS_FIX":
            return "Supervisor: asking for clarification"
        return "Supervisor: reply finalized"

    if name == "tool_started" and tool:
        if "intent" in tool:
            return "Intent extraction started"
        if "feas" in tool:
            return "Feasibility reasoning started"
        return f"Tool started: {tool}"

    if name == "tool_completed" and tool:
        if "intent" in tool:
            return "Intent extraction completed"
        if "feas" in tool:
            return "Feasibility reasoning completed"
        return f"Tool completed: {tool}"

    return name.replace("_", " ").title()


def _reasoning_body(event: EventRecord) -> str:
    name = event.name
    payload = event.payload
    state = payload.get("state") or payload.get("state_after") or payload.get("state_before")
    slots = (state or {}).get("slots", {}) if isinstance(state, dict) else {}
    feasibility = (state or {}).get("feasibility", {}) if isinstance(state, dict) else {}

    if name == "supervisor_evaluating_conversation":
        user = payload.get("user_text", "")
        what = slots.get("what")
        when = slots.get("when")
        pieces = []
        if what:
            pieces.append(f'WHAT: "{_short(str(what), 32)}"')
        if when:
            pieces.append(f'WHEN: "{_short(str(when), 32)}"')
        slot_summary = ", ".join(pieces) if pieces else "no structured WHAT/WHEN yet"
        return f'User said "{_short(str(user), 48)}"; {slot_summary}.'

    if name == "supervisor_completed_turn":
        reply = payload.get("assistant_reply", "")
        feas = feasibility.get("is_feasible")
        if feas is True:
            feas_str = "current plan looks feasible"
        elif feas is False:
            feas_str = "current plan is not feasible yet"
        else:
            feas_str = "feasibility not checked yet"
        return f'{feas_str}; reply: "{_short(str(reply), 60)}".'

    tool = payload.get("tool")
    if name == "tool_started" and tool:
        if "intent" in tool:
            return "Parsing the full conversation to update WHAT and WHEN."
        if "feas" in tool:
            return "Checking activities and sensors to see if this reminder is realistic."
        return f"Running tool {tool}."

    if name == "tool_completed" and tool:
        if "intent" in tool:
            what = slots.get("what")
            when = slots.get("when")
            parts = []
            if what:
                parts.append(f'WHAT = "{_short(str(what), 32)}"')
            if when:
                parts.append(f'WHEN = "{_short(str(when), 32)}"')
            summary = ", ".join(parts) if parts else "no changes to WHAT/WHEN."
            return f"Updated structured reminder details: {summary}"
        if "feas" in tool:
            feas = feasibility.get("is_feasible")
            issues = feasibility.get("issues") or []
            if feas is True:
                return "This looks doable with the current smart-home setup."
            if feas is False:
                issue = _short(issues[0], 80) if issues else "Model marked this as infeasible."
                return f"Found feasibility issues: {issue}"
            return "Feasibility check completed."

    if "assistant_reply" in payload:
        return f'Reply: "{_short(str(payload.get("assistant_reply")), 60)}".'
    if "user_text" in payload:
        return f'User: "{_short(str(payload.get("user_text")), 60)}".'
    return "Internal step."


def _reasoning_icon(event: EventRecord) -> str:
    name = event.name
    tool = event.payload.get("tool")
    if "supervisor" in name:
        return "🧠"
    if name.startswith("tool_started"):
        if tool and "intent" in tool:
            return "📝"
        if tool and "feas" in tool:
            return "📡"
        return "🛠️"
    if name.startswith("tool_completed"):
        if tool and "intent" in tool:
            return "✅"
        if tool and "feas" in tool:
            return "✅"
        return "✅"
    return "✨"


def build_reasoning_html(events: List[EventRecord]) -> str:
    if not events:
        return """
        <div id="reasoning-panel">
          <div class="reasoning-empty">
            <div class="reasoning-empty-icon">🧠</div>
            <div class="reasoning-empty-message">
              Ask something to see how the assistant thinks and which tools it invokes.
            </div>
          </div>
        </div>
        """

    # Only show the most recent 7 events to keep the trail short.
    recent = events[-7:]
    steps_html = ""
    for ev in recent:
        steps_html += f"""
        <div class="reason-step">
          <div class="reason-step-dot">{_reasoning_icon(ev)}</div>
          <div class="reason-step-title">{_reasoning_title(ev)}</div>
          <div class="reason-step-text">{_reasoning_body(ev)}</div>
        </div>
        """

    return f"""
    <div id="reasoning-panel">
      <div class="reasoning-subtitle">Live reasoning steps from the supervisor and tools.</div>
      <div class="reasoning-steps">{steps_html}</div>
    </div>
    """


def _history_to_string(messages: List[Dict[str, str]]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in messages)


def user_submit(user_message: str, history: List[Dict[str, str]]) -> tuple[str, List[Dict[str, str]]]:
    if not user_message.strip():
        return "", history
    new_history = history + [{"role": "user", "content": user_message.strip()}]
    return "", new_history


async def bot_respond(
    history: List[Dict[str, str]],
    wrapper: RunContextWrapper[ConversationState],
) -> tuple[List[Dict[str, str]], str, str, str, bool]:
    if not history:
        # No history yet: nothing to reason about, no code generated.
        return history, "", "", ""

    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    conversation_str = _history_to_string(history)

    try:
        result = await _assistant.handle_turn(last_user, conversation_str, wrapper)
    finally:
        # When reasoning trace is disabled, there is nothing to clean up here.
        pass

    reply = result.get("assistant_reply", "")
    new_history = history + [{"role": "assistant", "content": reply}]

    # Reasoning/event trace disabled for testing; leave panel empty.
    reasoning_html = ""
    generated_code_str = ""
    status_text = ""
    should_codegen = False

    # If the conversation has ended, signal that we should trigger code generation
    if "[ChatEnded]" in reply:
        status_text = "Generating code for this reminder..."
        should_codegen = True

    return new_history, reasoning_html, generated_code_str, status_text, should_codegen


def clear_chat(
    wrapper: RunContextWrapper[ConversationState],
) -> tuple[List[Dict[str, str]], str, str, str, bool, RunContextWrapper[ConversationState]]:
    wrapper.context = _new_conversation_state()
    return [], build_reasoning_html([]), "", "", False, wrapper


async def run_codegen(
    wrapper: RunContextWrapper[ConversationState],
    should_codegen: bool,
) -> tuple[str, str, bool]:
    """
    Second-stage callback to run code generation after the chat reply
    has already been shown. When should_codegen is False, this is a no-op.
    """
    if not should_codegen:
        # Do not change code/status; keep flag false.
        return "", "", False

    status_text = "Generating code for this reminder..."
    try:
        state_dict = (
            wrapper.context
            if isinstance(wrapper.context, dict)
            else wrapper.context.model_dump()
        )
        code_obj = await CodeGeneration.generate_code(state_dict)
        generated_code_str = json.dumps(code_obj, indent=2)
    except Exception as e:
        generated_code_str = f"[CODEGEN] Error generating code: {e}"
        status_text = "Code generation failed."
    else:
        status_text = "Code generation complete."

    return generated_code_str, status_text, False


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Agentic Reminder Assistant") as demo:
        # Inject custom CSS via HTML for Gradio versions that don't support the `css` kwarg
        gr.HTML(f"<style>{CUSTOM_CSS}</style>")

        gr.Markdown("# Agentic Reminder Assistant", elem_id="agent-header")
        gr.Markdown(
            "A multi-agent reminder assistant for elders in a smart home.",
            elem_id="agent-subtitle",
        )

        wrapper_state = gr.State(_wrapper)
        codegen_flag = gr.State(False)

        with gr.Row():
            with gr.Column(scale=4):
                chatbot = gr.Chatbot(
                    label="Conversation",
                    height=500,
                )

                with gr.Accordion(
                    "Reasoning trail",
                    open=False,
                    elem_id="reasoning-accordion",
                ):
                    reasoning_html = gr.HTML(value=build_reasoning_html([]))

                with gr.Accordion(
                    "Generated code (trigger + cancel)",
                    open=False,
                ):
                    code_box = gr.Textbox(
                        label="Generated code JSON",
                        lines=14,
                        interactive=False,
                        value="Code will appear here after the conversation ends with [ChatEnded].",
                    )
                    status_box = gr.Textbox(
                        label="Code generation status",
                        lines=1,
                        interactive=False,
                        value="",
                    )

                user_box = gr.Textbox(
                    placeholder="Describe the reminder you want (e.g., “Remind me to take my pills after breakfast”).",
                    label="Your message",
                    lines=2,
                )
                with gr.Row():
                    send_btn = gr.Button("Send", variant="primary")
                    clear_btn = gr.Button("Reset conversation")

        # Wire up interactions: user -> history, then bot -> reply + reasoning
        user_box.submit(user_submit, [user_box, chatbot], [user_box, chatbot]).then(
            bot_respond,
            [chatbot, wrapper_state],
            [chatbot, reasoning_html, code_box, status_box, codegen_flag],
        ).then(
            run_codegen,
            [wrapper_state, codegen_flag],
            [code_box, status_box, codegen_flag],
        )
        send_btn.click(user_submit, [user_box, chatbot], [user_box, chatbot]).then(
            bot_respond,
            [chatbot, wrapper_state],
            [chatbot, reasoning_html, code_box, status_box, codegen_flag],
        ).then(
            run_codegen,
            [wrapper_state, codegen_flag],
            [code_box, status_box, codegen_flag],
        )

        clear_btn.click(
            clear_chat,
            [wrapper_state],
            [chatbot, reasoning_html, code_box, status_box, codegen_flag, wrapper_state],
        )

    return demo


if __name__ == "__main__":
    app = build_app()
    app.queue().launch(share=True)