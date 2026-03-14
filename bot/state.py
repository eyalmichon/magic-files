from __future__ import annotations

from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field

_STATE_PATH = Path(__file__).resolve().parent.parent / "state.json"


class State(BaseModel):
    """Runtime state — persisted to state.json, mutated by the bot."""

    root_folder_id: str | None = None
    allowed_user_ids: list[int] = Field(default_factory=list)

    def save(self) -> None:
        _STATE_PATH.write_text(self.model_dump_json(indent=2))


@cache
def get_state() -> State:
    if _STATE_PATH.exists():
        return State.model_validate_json(_STATE_PATH.read_text())
    return State()
