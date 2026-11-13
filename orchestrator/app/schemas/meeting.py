from datetime import datetime

from pydantic import BaseModel, computed_field, field_validator


class MeetingCreate(BaseModel):
    title: str
    transcript: str = ""
    participants: list[str] = []
    duration_seconds: int = 0

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v


class MeetingUpdate(BaseModel):
    title: str | None = None
    transcript: str | None = None
    participants: list[str] | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("title must not be blank")
        return v


class MeetingResponse(BaseModel):
    id: str
    title: str
    transcript: str
    participants: list[str]
    duration_seconds: int
    created_at: datetime
    updated_at: datetime
    # Teams-Termin eines Agenten (services/teams_meetings) — bei Mitschnitten leer.
    quelle: str = "aufnahme"
    agent_id: str | None = None
    beginn: datetime | None = None
    ende: datetime | None = None
    status: str | None = None
    gaeste_extern: bool = False
    hinweis: str | None = None
    protokoll: str = ""

    @computed_field
    @property
    def aufgaben(self) -> list[str]:
        from app.core.projekt_termine import aktionspunkte

        return aktionspunkte(self.protokoll)

    model_config = {"from_attributes": True}


class MeetingListResponse(BaseModel):
    meetings: list[MeetingResponse]
    total: int
