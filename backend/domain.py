"""Strict transport contracts. Network access is deliberately absent here."""
from typing import Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import math

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Source(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=120)
    permission_reference: str = Field(min_length=10, max_length=2000)
    permitted_uses: list[Literal['display','analysis','training']] = Field(min_length=1)
    operator_attests_permission: bool
    @model_validator(mode='after')
    def permission(self):
        if not self.operator_attests_permission:
            raise ValueError('Explicit operator attestation of permission is required')
        return self

class Entry(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    no: int = Field(ge=1, le=9)
    name: str = Field(min_length=1,max_length=120)
    prefecture: str | None = Field(default=None,max_length=20)
    race_points: float | int | None = Field(default=None, ge=0,le=200)

class Race(Contract):
    venue: str = Field(min_length=1,max_length=100)
    region: str = Field(min_length=1,max_length=20)
    start: str
    @field_validator('start')
    @classmethod
    def valid_start(cls,v):
        timestamp(v)
        return v

class Roster(Contract):
    entries: list[Entry] = Field(min_length=2,max_length=9)
    @model_validator(mode='after')
    def unique(self):
        if len({e.id for e in self.entries})!=len(self.entries) or len({e.no for e in self.entries})!=len(self.entries):
            raise ValueError('Duplicate rider/car number')
        return self

class Lineup(Contract):
    lines: list[list[str]] = Field(min_length=1,max_length=9)
    @model_validator(mode='after')
    def unique(self):
        flat=[x for g in self.lines for x in g]
        if any(not g for g in self.lines) or len(flat)>9 or len(set(flat))!=len(flat):
            raise ValueError('Invalid or duplicate lineup')
        return self

class Stats(Contract):
    player_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    starts: int = Field(ge=1,le=100000)
    wins: int = Field(ge=0)
    top3: int = Field(ge=0)
    period_end: str
    @model_validator(mode='after')
    def counts(self):
        timestamp(self.period_end)
        if not self.wins<=self.top3<=self.starts:
            raise ValueError('Inconsistent statistics')
        return self

class Odds(Contract):
    type: Literal['2車単','3連単']
    selection: str = Field(pattern=r'^[1-9](?:-[1-9]){1,2}$')
    multiplier: float | int = Field(ge=1,le=1000000)
    quoted_at: str
    @model_validator(mode='after')
    def ticket(self):
        timestamp(self.quoted_at)
        validate_selection(self.type,self.selection)
        return self

class Payout(Contract):
    type: Literal['2車単','3連単']
    selection: str
    per100: int = Field(ge=0,le=100000000)
    @model_validator(mode='after')
    def ticket(self):
        validate_selection(self.type,self.selection)
        return self

class Result(Contract):
    order: list[str] = Field(min_length=2,max_length=9)
    payouts: list[Payout] = Field(max_length=100)
    result_at: str
    cancelled: bool = False
    @model_validator(mode='after')
    def result(self):
        timestamp(self.result_at)
        if len(set(self.order))!=len(self.order):
            raise ValueError('Ties must be represented by a future explicit tie contract; not supported')
        if len({(p.type,p.selection) for p in self.payouts})!=len(self.payouts):
            raise ValueError('Duplicate payout')
        return self

class Follow(Contract):
    leader: str
    follower: str
    position: int = Field(ge=2,le=9)
    followed: bool | None
    dropped: bool | None
    leader_pref: str | None = None
    follower_pref: str | None = None
    evidence_reference: str = Field(min_length=3,max_length=2000)
    result_at: str
    @model_validator(mode='after')
    def result(self):
        timestamp(self.result_at)
        if self.leader==self.follower or (self.followed is True and self.dropped is True):
            raise ValueError('Contradictory following result')
        return self

PAYLOADS={'race':Race,'roster':Roster,'lineup':Lineup,'stats':Stats,'odds':Odds,'result':Result,'follow':Follow}
class Record(Contract):
    kind: Literal['race','roster','lineup','stats','odds','result','follow']
    race_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    effective_at: str
    payload: dict
    @model_validator(mode='after')
    def payload_contract(self):
        timestamp(self.effective_at)
        self.payload=PAYLOADS[self.kind].model_validate(self.payload).model_dump()
        return self

class Dataset(Contract):
    source_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    records: list[Record] = Field(min_length=1,max_length=2000)

class NoteInput(Contract):
    player_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    content: str = Field(min_length=1,max_length=4000)
    evidence_reference: str = Field(default='',max_length=2000)
    evidence_excerpt: str = Field(default='',max_length=4000)

class NoteReview(Contract):
    revision: int = Field(ge=1)
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    reviewed_evidence_reference: str = Field(min_length=3,max_length=2000)
    reviewer: str = Field(min_length=1,max_length=120)

class ForecastRequest(Contract):
    race_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    stake_per_ticket: int = Field(default=100,ge=100,le=100000)
    @field_validator('stake_per_ticket')
    @classmethod
    def denomination(cls,v):
        if v%100:raise ValueError('100 yen increments required')
        return v

def timestamp(value):
    if not isinstance(value,str):raise ValueError('ISO8601 time required')
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('Timezone required')
    return t.timestamp()

def validate_selection(kind,selection):
    parts=selection.split('-')
    n=2 if kind=='2車単' else 3
    if len(parts)!=n or len(set(parts))!=n or not all(x in '123456789' and len(x)==1 for x in parts):
        raise ValueError('Invalid ticket selection')
