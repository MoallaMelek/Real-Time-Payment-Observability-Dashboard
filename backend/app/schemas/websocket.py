from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.schemas.dashboard import DashboardSnapshot


class SupervisionUpdatedEvent(BaseModel):
    type: Literal["supervision_update"] = "supervision_update"
    snapshot: DashboardSnapshot


SocketEvent = Annotated[SupervisionUpdatedEvent, Field(discriminator="type")]


class SnapshotSocketMessage(BaseModel):
    version: Literal["1.0"] = "1.0"
    type: Literal["snapshot"] = "snapshot"
    state_version: int
    emitted_at: datetime
    snapshot: DashboardSnapshot


class EventsSocketMessage(BaseModel):
    version: Literal["1.0"] = "1.0"
    type: Literal["events"] = "events"
    state_version: int
    emitted_at: datetime
    events: list[SocketEvent]


class PongSocketMessage(BaseModel):
    version: Literal["1.0"] = "1.0"
    type: Literal["pong"] = "pong"
    emitted_at: datetime


class ErrorSocketMessage(BaseModel):
    version: Literal["1.0"] = "1.0"
    type: Literal["error"] = "error"
    emitted_at: datetime
    detail: str
