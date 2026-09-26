from __future__ import annotations

from dataclasses import dataclass

from app.chatbot.temporal_resolver import TemporalRange


@dataclass(frozen=True, slots=True)
class FollowUpResolution:
    axis: str
    period: str
    compare_left: str | None = None
    compare_right: str | None = None
    temporal: TemporalRange | None = None
    metric_subject: str | None = None
    contextual: bool = False
