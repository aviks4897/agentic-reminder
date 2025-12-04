from typing import List, Optional, Union, Literal
from pydantic import BaseModel, model_validator, Field
from typing_extensions import Annotated
from enum import Enum
import datetime


# Trigger Models start from here
class RecurrenceDetails(BaseModel):
    # if delay, how long before the trigger can be triggered again
    delay: Optional[int] = None


class OccurrenceFrequency(str, Enum):
    once = "once"
    once_per_day = "once_per_day"
    always = "always"
    delay = "delay"


class Recurrence(BaseModel):
    repeat: bool
    details: Optional[RecurrenceDetails] = None
    occurrence_frequency: OccurrenceFrequency

    @model_validator(mode="after")
    def validate_recurrence(cls, model):
        # Validate `delay` when `occurrence_frequency` is "delay"
        if model.occurrence_frequency == OccurrenceFrequency.delay and (
                (model.details and model.details.delay) and model.details.delay < 0
        ):
            raise ValueError(
                "Delay must be greater than or equal to 0 when occurrence_frequency is 'delay'."
            )

        if model.repeat and model.occurrence_frequency == OccurrenceFrequency.once:
            raise ValueError(
                "If repeat is true, occurrence_frequency cannot be 'once'."
            )

        if model.repeat is False and model.occurrence_frequency != OccurrenceFrequency.once:
            raise ValueError(
                "If repeat is false, occurrence_frequency must be 'once'.")

        return model


class TriggerCondition(BaseModel):
    generated_trigger_code: str  # code that can be executed
    recurrence: Recurrence


class CancelCondition(BaseModel):
    delay: int  # how long before the closure condition should start checking
    generated_cancel_code: Optional[
        str]  # Optional code that will be executed when determining if the condition is met

    @model_validator(mode="after")
    def validate_cancel_condition(cls, model):
        # Validate `delay` to be greater than 0
        if model.delay < 0:
            raise ValueError("Delay must be greater than or equal to 0.")

        return model


class Conversation(BaseModel):
    role: str  # role of the participant (e.g., agent)
    content: str  # content of the conversation


class ConstructionInfo(BaseModel):
    conversations: List[Conversation]  # list of conversations
    summary: str  # summary of the construction info
    # additional analysis data (e.g., sensors, activities)
    analysis_data: Optional[dict] = None


class ReminderAction(BaseModel):
    type: Literal['reminder'] = "reminder",
    title: str  # title of the reminder
    content: str  # content of the reminder
    priority: int  # priority level of the reminder
    cancel_signal: bool = False  # whether to send a end signal to the reminder system
    # send to this device instead of the default home_id
    device_id: Optional[str] = None


class BroadcastAction(BaseModel):
    type: Literal['broadcast'] = "broadcast",
    location: str  # location where the broadcast should be sent
    eventName: str  # name of the event to broadcast
    topicName: str  # topic to broadcast to.
    payload: dict  # payload for the broadcast


class ConversationAction(BaseModel):
    type: Literal['ca'] = "ca",
    message: str  # message to be sent in the conversation
    explanation: str  # explanation of the message


class TriggerMachine(BaseModel):
    TriggerId: str  # unique id
    TriggerName: Optional[str] = None  # [Optional] name
    category: Optional[str] = None
    trigger_condition: TriggerCondition  # conditions for triggering
    # conditions for cancellation
    cancel_condition: Optional[CancelCondition] = None
    # additional construction information
    construction_info: Optional[ConstructionInfo] = None
    actions: List[Annotated[Union[ReminderAction, BroadcastAction, ConversationAction], Field(
        discriminator='type')]]  # list of actions to be performed


class HomeTriggerList(BaseModel):
    home_id: str  # unique id for the home
    home_name: Optional[str] = ""  # name of the home
    print_debug_info: bool = False   # whether to print debug info
    new_day_start_time: datetime.time  # time when a new day starts
    time_between_triggers: int
    TriggerMachines: List[TriggerMachine]  # list of trigger machines


# Payload Models start from here
class ReminderPayloadParameters(BaseModel):
    title: str
    content: str
    priority: int


class ReminderPayload(BaseModel):
    clientId: str
    parameters: ReminderPayloadParameters
