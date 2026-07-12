"""Versioned eval JSONL — synthetic escalation cases only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from control_plane.schemas import ChatMessage


class EvalExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: str
    citation_ids: list[str] = Field(min_length=1)
    required_keywords: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    messages: list[ChatMessage] = Field(min_length=1)
    expected: EvalExpected
    forbidden: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class PolicyDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    excerpt: str
    category: Literal["billing", "access", "outage", "refund", "security", "general"]


def load_jsonl(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            cases.append(EvalCase.model_validate(json.loads(line)))
        except Exception as e:
            raise ValueError(f"{path}:{i}: {e}") from e
    if not cases:
        raise ValueError(f"empty eval dataset: {path}")
    return cases


def load_policies(path: Path) -> dict[str, PolicyDoc]:
    raw: list[Any] = json.loads(path.read_text(encoding="utf-8"))
    docs = [PolicyDoc.model_validate(row) for row in raw]
    by_id = {d.id: d for d in docs}
    if len(by_id) != len(docs):
        raise ValueError(f"duplicate policy ids in {path}")
    return by_id
