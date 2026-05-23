"""Mescla de resultados e checkpoint do runner."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from run_roteiro import (  # noqa: E402
    CHECKPOINT_NAME,
    StepResult,
    merge_step_results,
    save_checkpoint,
)

ROOT = Path(__file__).resolve().parents[1]


def test_merge_step_results_overwrites_same_id():
    a = StepResult("7.0", "a", "q1", ok=True, attempts=1, answer="old")
    b = StepResult("8.0", "b", "q2", ok=True, attempts=1, answer="new8")
    c = StepResult("7.0", "a", "q1", ok=True, attempts=1, answer="new7")
    merged = merge_step_results([a, b], [c])
    assert [r.step_id for r in merged] == ["7.0", "8.0"]
    assert merged[0].answer == "new7"


def test_save_checkpoint_roundtrip(tmp_path):
    results = [StepResult("1.0", "t", "q", ok=True, attempts=1, answer="ok")]
    meta = {"status": "partial", "started_at": "2026-01-01"}
    path = save_checkpoint(tmp_path, results, meta)
    assert path.name == CHECKPOINT_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["meta"]["status"] == "partial"
    assert data["results"][0]["answer"] == "ok"
