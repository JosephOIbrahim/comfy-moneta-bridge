"""Typed model + mutation primitives for ComfyUI API-format workflows.

A ComfyUI "workflow" (also called "prompt" in ComfyUI internals) is a
JSON dict keyed by string node id. Each node has a ``class_type``, an
``inputs`` dict, and optionally a ``_meta`` dict for UI hints. Inputs
can be literal scalars (str, int, float, list) or output references
``[upstream_node_id, slot_index]``.

This module wraps the dict in a ``Workflow`` dataclass that preserves
node ordering and ``_meta``, and offers the mutation primitives the
agent tools call into: ``add_node``, ``set_input``, ``connect``,
``remove_node``, ``find_nodes_by_class``, and ``validate`` against a
``/object_info`` schema map.

Pure stdlib. No httpx, no anthropic, no mcp — this module is safe to
import in any tool path. The serialization contract is:
``Workflow.load(d).dump() == d`` (modulo dict-ordering preservation
via OrderedDict).

Hard Rule §14: ``validate`` is the gate used by ``workflow_submit``.
A non-empty error list means the workflow must not be submitted.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _is_connection(value: Any) -> bool:
    """A connection is ``[node_id, slot_index]`` — list/tuple of len 2,
    with a string-coercible node id and int-coercible slot."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    node_id, slot = value
    return isinstance(node_id, str) and isinstance(slot, int)


@dataclass
class Workflow:
    """Ordered, mutable view of a ComfyUI API-format workflow.

    ``nodes`` is an OrderedDict so round-tripping preserves the
    declaration order ComfyUI's UI editor produces. ``_comments`` holds
    any top-level keys that begin with ``_`` (e.g. ``_comment`` in
    ``demo/workflow.json``) so they survive load/dump.
    """

    nodes: OrderedDict[str, dict] = field(default_factory=OrderedDict)
    extras: OrderedDict[str, Any] = field(default_factory=OrderedDict)

    @classmethod
    def load(cls, payload: dict) -> "Workflow":
        if not isinstance(payload, dict):
            raise TypeError(
                f"Workflow.load expected dict, got {type(payload).__name__}"
            )
        nodes: OrderedDict[str, dict] = OrderedDict()
        extras: OrderedDict[str, Any] = OrderedDict()
        for key, value in payload.items():
            if key.startswith("_"):
                extras[key] = value
            else:
                if not isinstance(value, dict):
                    raise ValueError(
                        f"node {key!r} value must be a dict, "
                        f"got {type(value).__name__}"
                    )
                if "class_type" not in value:
                    raise ValueError(
                        f"node {key!r} missing required 'class_type'"
                    )
                nodes[key] = value
        return cls(nodes=nodes, extras=extras)

    @classmethod
    def load_path(cls, path: Path) -> "Workflow":
        with open(path, "r", encoding="utf-8") as fp:
            return cls.load(json.load(fp))

    def dump(self) -> dict:
        """Return the dict form. Extras precede nodes, matching the
        ``demo/workflow.json`` convention where ``_comment`` is first."""
        out: dict = {}
        for k, v in self.extras.items():
            out[k] = v
        for k, v in self.nodes.items():
            out[k] = v
        return out

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.nodes

    def __getitem__(self, node_id: str) -> dict:
        return self.nodes[node_id]

    def add_node(
        self,
        node_id: str,
        class_type: str,
        inputs: dict | None = None,
        _meta: dict | None = None,
    ) -> None:
        if node_id in self.nodes:
            raise ValueError(f"node {node_id!r} already exists")
        node: dict = {"class_type": class_type, "inputs": dict(inputs or {})}
        if _meta is not None:
            node["_meta"] = dict(_meta)
        self.nodes[node_id] = node

    def set_input(self, node_id: str, input_name: str, value: Any) -> None:
        if node_id not in self.nodes:
            raise KeyError(f"node {node_id!r} not in workflow")
        self.nodes[node_id].setdefault("inputs", {})[input_name] = value

    def connect(
        self,
        src_node: str,
        src_slot: int,
        dst_node: str,
        dst_input: str,
    ) -> None:
        if src_node not in self.nodes:
            raise KeyError(f"src node {src_node!r} not in workflow")
        if dst_node not in self.nodes:
            raise KeyError(f"dst node {dst_node!r} not in workflow")
        if not isinstance(src_slot, int) or src_slot < 0:
            raise ValueError(
                f"src_slot must be non-negative int, got {src_slot!r}"
            )
        self.set_input(dst_node, dst_input, [src_node, src_slot])

    def remove_node(self, node_id: str) -> None:
        """Remove a node and strip every connection referencing it.

        Dangling connections (other nodes' inputs pointing into the
        removed node) are deleted from those input dicts so the
        workflow stays consistent.
        """
        if node_id not in self.nodes:
            raise KeyError(f"node {node_id!r} not in workflow")
        del self.nodes[node_id]
        for downstream in self.nodes.values():
            inputs = downstream.get("inputs") or {}
            to_drop = [
                name
                for name, value in inputs.items()
                if _is_connection(value) and value[0] == node_id
            ]
            for name in to_drop:
                del inputs[name]

    def find_nodes_by_class(self, class_type: str) -> list[str]:
        return [
            nid
            for nid, node in self.nodes.items()
            if node.get("class_type") == class_type
        ]

    def validate(self, object_info: dict) -> list[str]:
        """Return a list of validation errors. Empty list == valid.

        Checks per node:
          1. ``class_type`` is present in ``object_info``.
          2. Every connection's source node exists in this workflow.
          3. Every ``required`` input declared by ``object_info`` is
             present on the node (either as a literal or as a
             connection).

        Optional inputs are not enforced — ComfyUI tolerates omission.
        Type-checking of literal values against the schema's declared
        types is intentionally out of scope for v0.2 (the executor will
        coerce or reject; the local validator only catches structural
        errors).
        """
        errors: list[str] = []
        for nid, node in self.nodes.items():
            class_type = node.get("class_type")
            if class_type not in object_info:
                errors.append(
                    f"node {nid!r}: unknown class_type {class_type!r}"
                )
                continue
            schema = object_info[class_type]
            required = (schema.get("input") or {}).get("required") or {}
            inputs = node.get("inputs") or {}
            for input_name in required:
                if input_name not in inputs:
                    errors.append(
                        f"node {nid!r} ({class_type}): missing required "
                        f"input {input_name!r}"
                    )
            for input_name, value in inputs.items():
                if _is_connection(value):
                    src_node_id = value[0]
                    if src_node_id not in self.nodes:
                        errors.append(
                            f"node {nid!r} input {input_name!r}: "
                            f"references missing node {src_node_id!r}"
                        )
        return errors
