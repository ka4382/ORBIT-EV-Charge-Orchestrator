"""The golden set in tests/agent_eval runs green in-process (offline mode, fakes)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parents[3] / "tests" / "agent_eval"


async def test_golden_questions_offline():
    spec = importlib.util.spec_from_file_location("orbit_agent_run_eval", EVAL_DIR / "run_eval.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    items = yaml.safe_load((EVAL_DIR / "golden.yaml").read_text())["questions"]
    assert len(items) == 20
    results = await mod.run_in_process(items, use_llm=False)
    failed = [(r["id"], r["failures"]) for r in results if not r["ok"]]
    assert failed == []
    assert not any(r["hallucinated"] for r in results)
    assert mod.report(results, verbose=False) == 1.0
