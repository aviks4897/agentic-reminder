from typing import Any, List, Literal, Optional
import json
import logging
import os
from pydantic import BaseModel, Field
from openai import OpenAI
from dotenv import load_dotenv


JSON_CONVERSION_SYSTEM_PROMPT = """

You are a skilled automation developer. You are tasked with converting the generated code and reminder summary provided to create a full home trigger file that contains all requirements
outlined in the schema provided for a specific reminder. 

You will be provided with sensor code for detecting the reminder and a JSON summary of the reminder, including its recurrence.


**Requirements**:

1. **Insert correct keys in provided schema**
        **Requirements**
            - Insert generated code into "generated_trigger_code" for sensor readings.
            - Insert recurrence information provided from "recurrence" dictionary in reminder, where "repeat" is True when "type" is not "once" and False everywhere else. Fill in the other keys accordingly.
            - Use "task" from reminder summary to name TriggerId. For naming, use the item the task is referring to and end with '(task name)_trigger_id_1'. 
                - Example: When the freezer door was open, TriggerId : freezer_door_trigger_1
            - TriggerName should follow a similar pattern and describe what generally the reminder is detecting (e.g. "freezer_door not closed").

2. **Create cancel code for reminders**
    
    Goal: Write a cancel function that returns True exactly when the reminder/event started by the trigger should be dismissed/considered ended; otherwise return False. 
    
    **Code Requirements**
        - Return type must be a boolean
            - True = Cancel/dismiss reminder, False = Keep reminder active
        - Function name should be 'def (reminder_name)_cancel_code'
        - Function inputs should mimic original trigger code
    **Design Choices**
        - Base code off logic from original trigger code provided and follow same pattern for determining if the trigger should be canceled (e.g. door closed after being open, 
        motion ceased for X seconds, value returned to normal range)
        - Add any imports you might need inside function
        - Keep code simple and concise
        - Delay should be 0 unless specified somewhere else.

3. **Handling actions and reminder message**

        **Requirements**
            - Priority should be on a 5 point scale. If a priority is labeled as "high", associate it with priority 4. If a priority is labeled "medium" associate it with priority 3 and add number into priority category of schema.
                - Quantify priority as best as you can and use the current wording label as a guide.
                - "very high" -> 5, "high" -> 4, "medium" -> 3, "low" -> 1 or 2
                - Examples:
                "Turn stove off" = 5, "Close fridge door" = 4, "Take food out of microwave" = 3
            - For "title" in schema insert what the trigger is set off by (e.g. "freezer door open")
            - For "content" in schema describe a short blurb on what should be done to resolve trigger (e.g. "Close freezer door"). Be concise, reminder should not be a full sentence.
            - For "end_signal" leave null unless specified



## Examples:

Input: 

Code: def freezer_door_left_open_trigger_code(time, sensor_data, activity_data, blackboard):\n                
contact_state = sensor_data['contact'].get('/home/kitchen/refrigerator/freezer_door', -1)\n                state = blackboard.get('state', 0)\n                if state == 0:\n                    if contact_state == 1:\n                        blackboard['state'] = 1\n                        blackboard['time'] = time\n                elif state == 1:\n                    if time - blackboard['time'] > datetime.timedelta(seconds=5):\n                        return True\n                    if contact_state == 0:\n                        blackboard['state'] = 0\n                return False"

Reminder: 

    {{
      "task": "Freezer door open,
      "date": "today",
      "time_inferred": null,
      "recurrence": {{
        "type": "once",
        "details": {{
          "days": null,
          "occurrence_frequency": "once"
        }}
      }},
      "priority": "high"
    }}

Output:
    
    {{
      "TriggerId": "freezer_door_trigger_1",
      "TriggerName": "freezer_door not closed",
      "trigger_condition": {{
        "generated_trigger_code": "def freezer_door_left_open_trigger_code(time, sensor_data, activity_data, blackboard):\n                contact_state = sensor_data['contact'].get('/home/kitchen/refrigerator/freezer_door', -1)\n                state = blackboard.get('state', 0)\n                if state == 0:\n                    if contact_state == 1:\n                        blackboard['state'] = 1\n                        blackboard['time'] = time\n                elif state == 1:\n                    if time - blackboard['time'] > datetime.timedelta(seconds=5):\n                        return True\n                    if contact_state == 0:\n                        blackboard['state'] = 0\n                return False",
        "recurrence": {{
          "repeat": true,
          "details": null,
          "occurrence_frequency": "always"
        }}
      }},
      "cancel_condition": {{
        "delay": 0,
        "generated_cancel_code": "def freezer_door_left_open_cancel_code(time, sensor_data, activity_data, blackboard):\n                contact_state = home_data['contact'].get('/home/kitchen/refrigerator/freezer_door', -1)\n                return contact_state != 1"
      }},
      "construction_info": null,
      "actions": [
        {{
          "type": "reminder",
          "title": "freezer door open",
          "content": "close the freezer door",
          "priority": 4,
          "end_signal": null
        }},
      
      ]
    }}


Keep close track of the reminder summary and generated code and how it relates to the schema overall. Fill in the necessary gaps.

"""


class Recurrence(BaseModel):
    repeat: bool
    details: Optional[Any] = None
    occurrence_frequency: Literal["always", "once", "multiple", "daily", "weekly", "monthly", "yearly"]


class TriggerCondition(BaseModel):
    generated_trigger_code: str
    recurrence: Recurrence


class CancelCondition(BaseModel):
    delay: int = Field()
    generated_cancel_code: str = Field(description="The code to determine when the trigger is canceled")


class Action(BaseModel):
    type: Literal["reminder"]
    title: str
    content: str
    priority: int
    end_signal: Optional[Any] = None


class TriggerDefinition(BaseModel):
    TriggerId: str
    TriggerName: str
    trigger_condition: TriggerCondition
    cancel_condition: CancelCondition
    construction_info: Optional[Any] = None
    actions: List[Action]


load_dotenv()
_client = OpenAI()


def _extract_output_text(resp: Any) -> str:
    """
    Robustly extract text from a Responses API response.
    """
    txt = getattr(resp, "output_text", None)
    if isinstance(txt, str) and txt.strip():
        return txt.strip()

    try:
        parts: List[str] = []
        for block in getattr(resp, "output", []) or []:
            for c in getattr(block, "content", []) or []:
                if isinstance(c, dict):
                    if c.get("type") == "output_text":
                        parts.append(c.get("text", ""))
                    elif "text" in c:
                        parts.append(str(c["text"]))
                else:
                    t = getattr(c, "text", None)
                    if isinstance(t, str):
                        parts.append(t)
        return "\n".join(parts).strip() if parts else ""
    except Exception:
        return ""


def generate_json(reminder: Any, code: str) -> str:
    """
    Use the OpenAI Responses API (gpt-5.1) to convert a reminder + trigger code
    into a structured TriggerDefinition JSON string.
    """
    logger = logging.getLogger(__name__)

    # Ensure reminder is a JSON string for the prompt
    reminder_str = reminder if isinstance(reminder, str) else json.dumps(reminder)

    # Build a single combined input; system instructions come from JSON_CONVERSION_SYSTEM_PROMPT
    input_text = f"Code:\n{code}\n\nReminder:\n{reminder_str}"

    # Configure response_format using the Pydantic schema
    schema = TriggerDefinition.model_json_schema()

    resp = _client.responses.create(
        model="gpt-5.1",
        instructions=JSON_CONVERSION_SYSTEM_PROMPT,
        input=input_text,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "trigger_definition",
                "schema": schema,
                "strict": True,
            },
        },
    )

    raw_text = _extract_output_text(resp)
    if not raw_text:
        raise RuntimeError("JSON conversion returned empty output_text")

    # Validate against TriggerDefinition for safety
    parsed = TriggerDefinition.model_validate_json(raw_text)
    json_str = parsed.model_dump_json(indent=2)

    logger.info("LLM structured result created")
    logger.debug("LLM structured JSON length: %d", len(json_str))

    # Return JSON string (callers can json.loads if they need a dict)
    return json_str
