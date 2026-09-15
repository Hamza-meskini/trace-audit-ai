"""One Boolean representation shared by extraction, verification and review."""

from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, ConfigDict, Field

ScalarValue = Union[float, str, bool]
ConditionValue = Union[ScalarValue, list[ScalarValue]]


class ConditionNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: Literal["CONDITION"]
    condition_id: str = Field(min_length=1)


class AllNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: Literal["ALL_OF"]
    children: list["LogicNode"] = Field(min_length=1)


class AnyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: Literal["ANY_OF"]
    children: list["LogicNode"] = Field(min_length=2)


class IfNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: Literal["IF_THEN"]
    antecedent: "LogicNode"
    consequent: "LogicNode"


LogicNode = Annotated[Union[ConditionNode, AllNode, AnyNode, IfNode], Field(discriminator="operator")]
for _node in (AllNode, AnyNode, IfNode):
    _node.model_rebuild()


def leaf_ids(tree: Any) -> list[str]:
    if not isinstance(tree, dict):
        return []
    if tree.get("operator") == "CONDITION":
        return [tree["condition_id"]] if tree.get("condition_id") else []
    nodes = tree.get("children") or [tree.get("antecedent"), tree.get("consequent")]
    return list(dict.fromkeys(item for node in nodes for item in leaf_ids(node)))


def contract_tree(contract: Any) -> dict:
    """Use flat fields only for contracts predating nested logic."""
    if contract.logic_tree is not None:
        return contract.logic_tree
    logic = contract.logic
    leaf = lambda identifier: {"operator": "CONDITION", "condition_id": identifier}
    if logic.operator == "IF_THEN":
        return {"operator": "IF_THEN", "antecedent": leaf(logic.if_condition_id),
                "consequent": {"operator": "ALL_OF", "children": [leaf(i) for i in logic.then_condition_ids]}}
    ids = logic.condition_ids or [c.condition_id for c in contract.atomic_conditions if c.mandatory]
    return {"operator": logic.operator, "children": [leaf(i) for i in ids]}


def flat_projection(tree: dict) -> dict | None:
    """Return a legacy representation only if it preserves every Boolean gate."""
    op = tree.get("operator")
    if op == "CONDITION":
        return {"operator": "ALL_OF", "condition_ids": leaf_ids(tree)}
    if op in {"ALL_OF", "ANY_OF"} and all(c.get("operator") == "CONDITION" for c in tree.get("children", [])):
        return {"operator": op, "condition_ids": leaf_ids(tree)}
    if op == "IF_THEN":
        ant, con = tree.get("antecedent") or {}, tree.get("consequent") or {}
        projected = flat_projection(con)
        if ant.get("operator") == "CONDITION" and projected and projected["operator"] == "ALL_OF":
            return {"operator": op, "condition_ids": leaf_ids(tree), "if_condition_id": ant.get("condition_id"),
                    "then_condition_ids": projected["condition_ids"]}
    return None


def supporting_path(tree: dict, by_id: dict) -> tuple[list, list[str]]:
    """Find a complete supported branch, retaining its applicability proof."""
    op = tree.get("operator")
    if op == "CONDITION":
        identifier = tree.get("condition_id") or "missing condition ID"
        result = by_id.get(identifier)
        return ([result] if result else [], [] if result and result.status in {"PROVEN", "NOT_APPLICABLE"} else [identifier])
    if op == "IF_THEN":
        ant = tree.get("antecedent") or {}
        proof, missing = supporting_path(ant, by_id)
        # An explicitly inapplicable trigger short-circuits this obligation.
        if not missing and proof and all(r.status == "NOT_APPLICABLE" for r in proof):
            return proof, []
        con_proof, con_missing = supporting_path(tree.get("consequent") or {}, by_id)
        return proof + con_proof, missing + con_missing
    if op in {"ALL_OF", "ANY_OF"}:
        paths = [supporting_path(c, by_id) for c in tree.get("children", [])]
        if op == "ANY_OF":
            for proof, missing in paths:
                if not missing and any(r.status == "PROVEN" for r in proof):
                    return proof, []
            return [r for p, _ in paths for r in p], leaf_ids(tree) or ["empty ANY_OF"]
        if paths:
            return [r for p, _ in paths for r in p], list(dict.fromkeys(i for _, m in paths for i in m))
    return [], ["invalid logic tree"]
