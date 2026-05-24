"""Tests for ``comfy_moneta_bridge.agents.workflow``.

Pure stdlib; no extras gating needed. CRUCIBLE bias: round-trip tests
assert ordering preservation, the remove_node test asserts dangling
connections are scrubbed, validation tests cover the three error
classes (unknown class, missing required input, dangling connection
source).
"""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

import pytest

from comfy_moneta_bridge.agents.workflow import Workflow, _is_connection

DEMO_WORKFLOW = (
    Path(__file__).resolve().parent.parent / "demo" / "workflow.json"
)


def _load_demo() -> dict:
    with open(DEMO_WORKFLOW, encoding="utf-8") as fp:
        return json.load(fp)


def test_load_demo_workflow() -> None:
    wf = Workflow.load(_load_demo())
    assert "3" in wf
    assert wf["3"]["class_type"] == "KSampler"


def test_round_trip_preserves_order() -> None:
    original = _load_demo()
    wf = Workflow.load(original)
    dumped = wf.dump()
    assert list(dumped.keys()) == list(original.keys())


def test_round_trip_preserves_extras() -> None:
    original = _load_demo()
    wf = Workflow.load(original)
    assert "_comment" in wf.extras
    assert wf.dump()["_comment"] == original["_comment"]


def test_round_trip_preserves_meta() -> None:
    payload = {
        "1": {
            "class_type": "KSampler",
            "inputs": {"seed": 42},
            "_meta": {"title": "main sampler"},
        }
    }
    wf = Workflow.load(payload)
    assert wf.dump()["1"]["_meta"] == {"title": "main sampler"}


def test_load_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        Workflow.load([1, 2, 3])  # type: ignore[arg-type]


def test_load_rejects_non_dict_node_value() -> None:
    with pytest.raises(ValueError, match="must be a dict"):
        Workflow.load({"3": "not a dict"})


def test_load_rejects_node_missing_class_type() -> None:
    with pytest.raises(ValueError, match="class_type"):
        Workflow.load({"3": {"inputs": {}}})


def test_add_node() -> None:
    wf = Workflow()
    wf.add_node("1", "KSampler", inputs={"seed": 0})
    assert wf["1"]["class_type"] == "KSampler"
    assert wf["1"]["inputs"]["seed"] == 0


def test_add_node_duplicate_raises() -> None:
    wf = Workflow()
    wf.add_node("1", "KSampler")
    with pytest.raises(ValueError, match="already exists"):
        wf.add_node("1", "OtherClass")


def test_set_input_existing_node() -> None:
    wf = Workflow.load(_load_demo())
    wf.set_input("3", "seed", 99)
    assert wf["3"]["inputs"]["seed"] == 99


def test_set_input_unknown_node_raises() -> None:
    wf = Workflow.load(_load_demo())
    with pytest.raises(KeyError):
        wf.set_input("999", "seed", 1)


def test_connect_creates_reference() -> None:
    wf = Workflow.load(_load_demo())
    wf.connect("4", 0, "3", "model")
    assert wf["3"]["inputs"]["model"] == ["4", 0]


def test_connect_rejects_missing_src() -> None:
    wf = Workflow.load(_load_demo())
    with pytest.raises(KeyError, match="src"):
        wf.connect("999", 0, "3", "model")


def test_connect_rejects_missing_dst() -> None:
    wf = Workflow.load(_load_demo())
    with pytest.raises(KeyError, match="dst"):
        wf.connect("4", 0, "999", "model")


def test_connect_rejects_negative_slot() -> None:
    wf = Workflow.load(_load_demo())
    with pytest.raises(ValueError, match="non-negative"):
        wf.connect("4", -1, "3", "model")


def test_remove_node_clears_dangling_connections() -> None:
    wf = Workflow.load(_load_demo())
    # Node 4 (CheckpointLoaderSimple) feeds into 3, 6, 7, 8. Remove it.
    wf.remove_node("4")
    assert "4" not in wf
    # The "model" input of node 3 referenced ["4", 0] — should be gone.
    assert "model" not in wf["3"]["inputs"]
    # The "clip" input of node 6 referenced ["4", 1] — should be gone.
    assert "clip" not in wf["6"]["inputs"]


def test_remove_node_preserves_literal_inputs() -> None:
    wf = Workflow.load(_load_demo())
    wf.remove_node("4")
    # Node 3's literal inputs (seed, steps, cfg, etc.) remain.
    assert wf["3"]["inputs"]["seed"] == 42
    assert wf["3"]["inputs"]["steps"] == 20


def test_remove_node_unknown_raises() -> None:
    wf = Workflow.load(_load_demo())
    with pytest.raises(KeyError):
        wf.remove_node("999")


def test_find_nodes_by_class() -> None:
    wf = Workflow.load(_load_demo())
    text_encoders = wf.find_nodes_by_class("CLIPTextEncode")
    assert set(text_encoders) == {"6", "7"}
    samplers = wf.find_nodes_by_class("KSampler")
    assert samplers == ["3"]


def test_find_nodes_by_class_no_match() -> None:
    wf = Workflow.load(_load_demo())
    assert wf.find_nodes_by_class("NoSuchClass") == []


# --- Validation ---


def _object_info_for_demo() -> dict:
    """Mock /object_info with just enough to validate demo/workflow.json."""
    return {
        "KSampler": {
            "input": {
                "required": {
                    "model": [],
                    "positive": [],
                    "negative": [],
                    "latent_image": [],
                    "seed": [],
                    "steps": [],
                    "cfg": [],
                    "sampler_name": [],
                    "scheduler": [],
                    "denoise": [],
                }
            }
        },
        "CheckpointLoaderSimple": {
            "input": {"required": {"ckpt_name": []}}
        },
        "EmptyLatentImage": {
            "input": {
                "required": {"width": [], "height": [], "batch_size": []}
            }
        },
        "CLIPTextEncode": {
            "input": {"required": {"clip": [], "text": []}}
        },
        "VAEDecode": {
            "input": {"required": {"samples": [], "vae": []}}
        },
        "SaveImage": {
            "input": {
                "required": {"images": [], "filename_prefix": []}
            }
        },
    }


def test_validate_demo_workflow_clean() -> None:
    wf = Workflow.load(_load_demo())
    errors = wf.validate(_object_info_for_demo())
    assert errors == []


def test_validate_unknown_class_type() -> None:
    wf = Workflow()
    wf.add_node("1", "TotallyMadeUpClass")
    errors = wf.validate(_object_info_for_demo())
    assert any("unknown class_type" in e for e in errors)
    assert any("TotallyMadeUpClass" in e for e in errors)


def test_validate_missing_required_input() -> None:
    wf = Workflow()
    wf.add_node("1", "KSampler", inputs={"seed": 0})
    errors = wf.validate(_object_info_for_demo())
    # Many required inputs missing; should report at least one.
    assert any("missing required" in e for e in errors)


def test_validate_dangling_connection() -> None:
    wf = Workflow()
    wf.add_node(
        "1",
        "KSampler",
        inputs={
            "model": ["999", 0],  # references nonexistent node
            "positive": ["999", 0],
            "negative": ["999", 0],
            "latent_image": ["999", 0],
            "seed": 0,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
        },
    )
    errors = wf.validate(_object_info_for_demo())
    assert any("missing node" in e for e in errors)


# --- _is_connection helper ---


@pytest.mark.parametrize(
    "value,expected",
    [
        (["4", 0], True),
        (["4", 1], True),
        ([4, 0], False),  # node_id not str
        (["4", "0"], False),  # slot not int
        (["4"], False),  # wrong length
        ("string", False),
        (42, False),
        ({"node": 4}, False),
    ],
)
def test_is_connection(value, expected) -> None:
    assert _is_connection(value) is expected
