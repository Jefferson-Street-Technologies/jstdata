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
    should_prompt,
)

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
    "should_prompt",
]
