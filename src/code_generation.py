import json
from openai import OpenAI
from typing import Any
from dotenv import load_dotenv


from prompts import CODE_GENERATION_PROMPT

load_dotenv()


def _extract_output_text(resp: Any) -> str:
    """
    Robustly extract the assistant text from a Responses API object.
    Mirrors the logic used in run-chat-assistant, avoiding None indexing.
    """
    # Prefer .output_text if the SDK exposes it
    txt = getattr(resp, "output_text", None)
    if txt:
        return txt.strip()

    # Fallback: traverse response.output structure
    try:
        parts = []
        for block in getattr(resp, "output", []) or []:
            for c in getattr(block, "content", []) or []:
                # Newer SDKs may expose text directly
                if isinstance(c, dict):
                    if c.get("type") == "output_text":
                        parts.append(c.get("text", ""))
                    elif "text" in c:
                        parts.append(c["text"])
                else:
                    # Fallback for object-like content with .text
                    t = getattr(c, "text", None)
                    if isinstance(t, str):
                        parts.append(t)
        return "\n".join(parts).strip() if parts else ""
    except Exception:
        return ""


class CodeGeneration:

    async def generate_code(state: dict) -> Any:
        """
        Call the Responses API with CODE_GENERATION_PROMPT and return the parsed JSON object
        containing generated_trigger_code and generated_cancel_code.
        """
        client = OpenAI()

        resp = client.responses.create(
            model="gpt-5.1",
            instructions=CODE_GENERATION_PROMPT,
            reasoning={"effort": "medium"},
            input=json.dumps(state),
        )

        raw_text = _extract_output_text(resp)
        if not raw_text:
            raise RuntimeError("Code generation returned empty output_text")

        # The prompt requires a single JSON object; parse and return it.
        return json.loads(raw_text)


if __name__ == "__main__":
    import asyncio

    # Example dummy state; replace with whatever your code-gen prompt expects
    example_state = {

        "what": "take out the trash",
        "when": {
            "time_inferred": None,
            "exact_time": {
                "start_time": "8:00",
                "end_time": "8:00"
            }

        },
        "recurrence": None,
        "constraints": [],
        "priority": "normal",
        "channel": "default",
        "metadata": {},
 
    }

    async def _test():
        result = await CodeGeneration.generate_code(example_state)
        print("Generated code:\n", result)

    asyncio.run(_test())