"""Pure validation for the graph.json contract."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

CONTROLLED_CLASSES = frozenset(
    {
        "Organization",
        "Person",
        "GovProject",
        "Consortium",
        "SubProject",
        "System",
        "Agency",
        "Product",
        "Dataset",
        "Domain",
        "Credential",
        "Card",
        "BankAccount",
    }
)
CONTROLLED_RELATIONS = frozenset(
    {
        "about",
        "employedBy",
        "allocatedTo",
        "managesProject",
        "hasPI",
        "hasContact",
        "participatesIn",
        "fundedBy",
        "settledBy",
        "usesSystem",
        "partnerOf",
        "hasCredential",
        "paysWith",
        "depositsTo",
        "hasNationalResearcherId",
    }
)
SENSITIVE_CLASSES = frozenset({"Credential", "Card", "BankAccount"})
SENSITIVE_RELATIONS = frozenset(
    {"hasCredential", "paysWith", "depositsTo", "hasNationalResearcherId"}
)


class GraphInvariantError(ValueError):
    """Raised when a blocking graph invariant is violated."""

    def __init__(self, invariant: int, message: str, violations: Iterable[str]):
        self.invariant = invariant
        self.violations = tuple(violations)
        super().__init__(f"C4.4 invariant #{invariant}: {message}: {', '.join(self.violations)}")


class SensitiveDataError(GraphInvariantError):
    """Raised immediately when export-prohibited graph data is detected."""

    alert = True


def _items(graph: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = graph.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise GraphInvariantError(3, f"{key} must be a list of objects", [key])
    return value


def verify_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Enforce all C4.4 invariants and return non-blocking promotions."""
    if not isinstance(graph, Mapping):
        raise GraphInvariantError(3, "graph root must be an object", [type(graph).__name__])

    nodes = _items(graph, "nodes")
    edges = _items(graph, "edges")

    sensitive_classes = sorted(
        {str(node.get("class", "")) for node in nodes} & SENSITIVE_CLASSES
    )
    sensitive_relations = sorted(
        {str(edge.get("rel", "")) for edge in edges} & SENSITIVE_RELATIONS
    )
    if sensitive_classes or sensitive_relations:
        findings = [f"class:{name}" for name in sensitive_classes]
        findings.extend(f"rel:{name}" for name in sensitive_relations)
        raise SensitiveDataError(4, "ALERT sensitive graph data detected", findings)

    node_ids = [node.get("id") for node in nodes]
    duplicate_ids = sorted(
        str(node_id) for node_id, count in Counter(node_ids).items() if count > 1
    )
    if duplicate_ids:
        raise GraphInvariantError(1, "duplicate node ids", duplicate_ids)

    node_id_set = set(node_ids)
    missing_from = sorted(
        {str(edge.get("from", "")) for edge in edges if edge.get("from") not in node_id_set}
    )
    if missing_from:
        raise GraphInvariantError(2, "edge sources absent from nodes", missing_from)

    invalid_classes = sorted(
        {str(node.get("class", "")) for node in nodes if node.get("class") not in CONTROLLED_CLASSES}
    )
    invalid_relations = sorted(
        {str(edge.get("rel", "")) for edge in edges if edge.get("rel") not in CONTROLLED_RELATIONS}
    )
    malformed_nodes = sorted(
        str(index)
        for index, node in enumerate(nodes)
        if not isinstance(node.get("id"), str)
        or not node["id"]
        or not isinstance(node.get("businessKey"), str)
        or not isinstance(node.get("path"), str)
    )
    malformed_edges = sorted(
        str(index)
        for index, edge in enumerate(edges)
        if not isinstance(edge.get("from"), str)
        or not edge["from"]
        or not isinstance(edge.get("to"), str)
        or not edge["to"]
    )
    vocabulary_errors = [f"class:{name}" for name in invalid_classes]
    vocabulary_errors.extend(f"rel:{name}" for name in invalid_relations)
    vocabulary_errors.extend(f"node[{index}]" for index in malformed_nodes)
    vocabulary_errors.extend(f"edge[{index}]" for index in malformed_edges)
    if vocabulary_errors:
        raise GraphInvariantError(3, "invalid controlled vocabulary or shape", vocabulary_errors)

    promotion_candidates = sorted(
        {str(edge["to"]) for edge in edges if edge["to"] not in node_id_set}
    )
    return {
        "valid": True,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "promotion_candidates": promotion_candidates,
    }

