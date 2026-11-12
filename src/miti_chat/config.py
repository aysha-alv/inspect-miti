"""Load the protocol files: run plan, persona versions, prompts, manual and schemas.

assets/run_plan.json is the source of truth for operational settings. Paths in
it are relative to the repository root, as in the handoff folder.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_PLAN = "assets/run_plan.json"
DEFAULT_PRICING = "config/pricing.json"


class ConfigError(ValueError):
    """A protocol file is missing or inconsistent."""


@dataclass(frozen=True)
class BatchItem:
    scenario_id: str
    target_role: str


@dataclass(frozen=True)
class Batch:
    batch_id: str
    items: tuple[BatchItem, ...]
    client_role: str
    judge_role: str
    max_assistant_replies: int
    max_generated_client_followups: int
    raw: dict[str, Any] = field(repr=False)

    @property
    def roles(self) -> list[str]:
        """Every model role this batch uses (targets, client, judge)."""
        roles = [i.target_role for i in self.items] + [self.client_role, self.judge_role]
        return list(dict.fromkeys(roles))


class Protocol:
    """Read-only view of the protocol files in a repository checkout."""

    def __init__(self, root: Path | str = ROOT, run_plan: str = DEFAULT_RUN_PLAN,
                 pricing: str = DEFAULT_PRICING) -> None:
        self.root = Path(root).resolve()
        self.run_plan_file = run_plan
        self.pricing_file = pricing
        self.plan: dict[str, Any] = self.json(run_plan)
        self.version: str = self.plan["protocol_version"]

    # ---- file access -------------------------------------------------
    def path(self, rel: str) -> Path:
        p = self.root / rel
        if not p.is_file():
            raise ConfigError(f"missing protocol file: {rel}")
        return p

    def text(self, rel: str) -> str:
        return self.path(rel).read_text(encoding="utf-8")

    def json(self, rel: str) -> Any:
        return json.loads(self.text(rel))

    # ---- sections of the run plan ------------------------------------
    @property
    def generation(self) -> dict[str, Any]:
        return self.plan["generation"]

    @property
    def judging(self) -> dict[str, Any]:
        return self.plan["judging"]

    @property
    def execution(self) -> dict[str, Any]:
        return self.plan["execution"]

    @property
    def model_ids(self) -> dict[str, str | None]:
        return self.plan["model_ids"]

    def token_limit(self, key: str) -> int | None:
        """Response token limit for 'target', 'client', 'judge_globals' or 'judge_behaviors'."""
        return self.generation["response_token_limits"].get(key)

    def reasoning_effort(self, role: str) -> str | None:
        """Requested reasoning effort for a model role (e.g. 'target_openai'); None if not set."""
        return (self.generation.get("reasoning_effort") or {}).get(role)

    # ---- prompts -----------------------------------------------------
    @cached_property
    def target_system(self) -> str:
        return self.text(self.generation["target_system_file"]).strip()

    @cached_property
    def client_system(self) -> str:
        return self.text(self.generation["client_system_file"]).strip()

    @cached_property
    def manual(self) -> str:
        return self.text(self.judging["manual_text_file"])

    def judge_prompt_files(self, pass_name: str) -> list[str]:
        """System-prompt parts for a judge pass, in order: common, pass-specific, manual."""
        j = self.judging
        specific = {"globals": j["global_prompt_file"], "behaviors": j["behavior_prompt_file"]}[pass_name]
        return [j["common_prompt_file"], specific, j["manual_text_file"]]

    def schema_file(self, pass_name: str) -> str:
        j = self.judging
        return {"globals": j["global_schema_file"], "behaviors": j["behavior_schema_file"]}[pass_name]

    def schema(self, pass_name: str) -> dict[str, Any]:
        return self.json(self.schema_file(pass_name))

    # ---- personas ----------------------------------------------------
    @cached_property
    def scenarios(self) -> dict[str, dict[str, Any]]:
        """All persona versions from the run plan's persona_files, keyed by scenario_id."""
        out: dict[str, dict[str, Any]] = {}
        required = {"scenario_id", "persona_family_id", "readiness", "opening_type",
                    "private_profile", "opening_message", "response_guides"}
        for rel in self.plan["persona_files"]:
            data = self.json(rel)
            if data.get("protocol_version") != self.version:
                raise ConfigError(f"{rel}: protocol_version {data.get('protocol_version')!r} "
                                  f"does not match run plan {self.version!r}")
            for s in data["scenarios"]:
                missing = required - s.keys()
                if missing:
                    raise ConfigError(f"{rel}: scenario {s.get('scenario_id')!r} missing {sorted(missing)}")
                sid = s["scenario_id"]
                if sid in out:
                    raise ConfigError(f"duplicate scenario_id {sid!r} (in {rel})")
                out[sid] = {**s, "_source_file": rel}
        return out

    # ---- batches -----------------------------------------------------
    def batch(self, batch_id: str) -> Batch:
        """Find a batch definition by batch_id anywhere at the top level of the run plan."""
        for value in self.plan.values():
            if isinstance(value, dict) and value.get("batch_id") == batch_id:
                items = tuple(BatchItem(i["scenario_id"], i["target_role"]) for i in value["items"])
                for item in items:
                    if item.scenario_id not in self.scenarios:
                        raise ConfigError(f"batch {batch_id}: unknown scenario_id {item.scenario_id!r}")
                return Batch(
                    batch_id=batch_id,
                    items=items,
                    client_role=value["client_role"],
                    judge_role=value["judge_role"],
                    # a batch may set its own conversation length; otherwise the generation defaults apply
                    max_assistant_replies=int(value.get("max_assistant_replies",
                                                        self.generation["max_assistant_replies"])),
                    max_generated_client_followups=int(value.get("max_generated_client_followups",
                                                                 self.generation["max_generated_client_followups"])),
                    raw=value,
                )
        raise ConfigError(f"no batch with batch_id {batch_id!r} in {self.run_plan_file}")

    def batch_ids(self) -> list[str]:
        return [v["batch_id"] for v in self.plan.values() if isinstance(v, dict) and "batch_id" in v]

    # ---- pricing -----------------------------------------------------
    @cached_property
    def pricing(self) -> dict[str, dict[str, Any]]:
        p = self.root / self.pricing_file
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8")).get("models", {})

    # ---- files whose hashes are recorded with every run --------------
    def input_files(self) -> list[str]:
        g, j = self.generation, self.judging
        files = [self.run_plan_file, *self.plan["persona_files"],
                 g["target_system_file"], g["client_system_file"],
                 j["common_prompt_file"], j["global_prompt_file"], j["behavior_prompt_file"],
                 j["manual_text_file"], j["global_schema_file"], j["behavior_schema_file"]]
        for optional in (self.pricing_file, "config/client_checks.json"):
            if (self.root / optional).is_file():
                files.append(optional)
        return list(dict.fromkeys(files))
