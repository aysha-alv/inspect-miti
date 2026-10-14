"""Output layout for one batch.

runs/<batch_id>/
  transcripts/                 blinded public transcripts (<id>.json + <id>.txt)
  coding/<scoring_run_id>/     judge records, checks, counts and TSV exports
  private/                     NOT for blinded coding: keep out of anything sent to coders
    ledger.json                every provider attempt: settings, usage, cost, raw text
    conversations/<id>.json    scenario, models, stop reason, lengths, per-message attempts
    transcript_key.csv         transcript ID -> scenario / target role / models
    manifests/                 provenance for each generate/score run
    inspect_logs/              Inspect .eval logs (contain persona prompts and model calls)
"""

from __future__ import annotations

import csv
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT

KEY_FIELDS = ["transcript_id", "scenario_id", "persona_family_id", "target_role", "target_model",
              "client_model", "generation_run_id", "status", "stop_reason"]


def run_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(2)}"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class RunPaths:
    root: Path

    @classmethod
    def for_batch(cls, batch_id: str, runs_dir: Path | None = None) -> "RunPaths":
        return cls((runs_dir or ROOT / "runs") / batch_id)

    @property
    def private(self) -> Path:
        return self.root / "private"

    @property
    def ledger(self) -> Path:
        return self.private / "ledger.json"

    @property
    def conversations(self) -> Path:
        return self.private / "conversations"

    @property
    def key_file(self) -> Path:
        return self.private / "transcript_key.csv"

    @property
    def manifests(self) -> Path:
        return self.private / "manifests"

    @property
    def inspect_logs(self) -> Path:
        return self.private / "inspect_logs"

    @property
    def transcripts(self) -> Path:
        return self.root / "transcripts"

    @property
    def coding(self) -> Path:
        return self.root / "coding"

    def conversation_records(self) -> list[dict[str, Any]]:
        if not self.conversations.is_dir():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.conversations.glob("*.json"))]

    def append_key(self, row: dict[str, Any]) -> None:
        self.private.mkdir(parents=True, exist_ok=True)
        new = not self.key_file.exists()
        with self.key_file.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=KEY_FIELDS, delimiter=",")
            if new:
                w.writeheader()
            w.writerow({k: row.get(k) for k in KEY_FIELDS})
