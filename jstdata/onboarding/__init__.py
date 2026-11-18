"""First-run onboarding: coached tutorials hosted inside the investigation TUI."""

from .tutorials import (
    LEVELS,
    CoachSnapshot,
    Tutorial,
    TutorialCoach,
    TutorialPhase,
    completed_tutorials,
    list_tutorials,
    load_tutorial,
    mark_completed,
    mark_prompted,
    set_tips_enabled,
    should_prompt,
    tips_enabled,
)
from .tips import tip_for

__all__ = [
    "LEVELS",
    "CoachSnapshot",
    "Tutorial",
    "TutorialCoach",
    "TutorialPhase",
    "completed_tutorials",
    "list_tutorials",
    "load_tutorial",
    "mark_completed",
    "mark_prompted",
    "set_tips_enabled",
    "should_prompt",
    "tip_for",
    "tips_enabled",
]
