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
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=2))
        tmp.replace(_STATE_PATH)


@cache
def get_state() -> State:
    if _STATE_PATH.exists():
        return State.model_validate_json(_STATE_PATH.read_text())
    return State()
