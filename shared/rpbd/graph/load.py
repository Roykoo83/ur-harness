"""Idempotent graph.json loader for Neo4j Community 5.x."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping

from .verify import verify_graph

NODE_CYPHER = """
UNWIND $nodes AS n
MERGE (x {id: n.id})
SET x.class = n.class, x.businessKey = n.businessKey, x.path = n.path
"""
EDGE_CYPHER = """
UNWIND $edges AS e
MATCH (a {id: e.from})
MERGE (b {id: e.to})
CALL apoc.merge.relationship(a, e.rel, {}, {}, b) YIELD rel
RETURN count(rel) AS relationship_count
"""
SNAPSHOT_CYPHER = """
MATCH (n)
WITH collect({id: n.id, class: n.class, businessKey: n.businessKey, path: n.path}) AS nodes
OPTIONAL MATCH (a)-[r]->(b)
RETURN nodes, collect({from: a.id, rel: type(r), to: b.id}) AS edges
"""
COUNT_CYPHER = """
MATCH (n)
WITH count(n) AS nodes
CALL { MATCH ()-[r]->() RETURN count(r) AS edges }
RETURN nodes, edges
"""


def read_graph(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a UTF-8 graph snapshot from disk."""
    with Path(path).open(encoding="utf-8") as handle:
        graph = json.load(handle)
    verify_graph(graph)
    return graph


def _edge_key(edge: Mapping[str, Any]) -> tuple[str, str, str]:
    return str(edge["from"]), str(edge["rel"]), str(edge["to"])


def calculate_load_plan(
    graph: Mapping[str, Any],
    existing_nodes: Iterable[Mapping[str, Any]] = (),
    existing_edges: Iterable[Mapping[str, Any]] = (),
    mode: str = "full",
) -> dict[str, Any]:
    """Calculate deterministic MERGE inputs without mutating any database."""
    validation = verify_graph(graph)
    if mode not in {"full", "incremental"}:
        raise ValueError("mode must be 'full' or 'incremental'")

    source_nodes = list(graph.get("nodes", []))
    source_edges = list(graph.get("edges", []))
    existing_by_id = {str(node.get("id")): dict(node) for node in existing_nodes}
    existing_edge_keys = {_edge_key(edge) for edge in existing_edges}

    if mode == "full":
        nodes_to_merge = source_nodes
        edges_to_merge = source_edges
    else:
        compared_fields = ("class", "businessKey", "path")
        nodes_to_merge = [
            node
            for node in source_nodes
            if str(node["id"]) not in existing_by_id
            or any(existing_by_id[str(node["id"])].get(key) != node.get(key) for key in compared_fields)
        ]
        edges_to_merge = [edge for edge in source_edges if _edge_key(edge) not in existing_edge_keys]

    orphaned = sorted(set(existing_by_id) - {str(node["id"]) for node in source_nodes})
    return {
        "mode": mode,
        "nodes": nodes_to_merge,
        "edges": edges_to_merge,
        "orphaned": orphaned,
        "promotion_candidates": validation["promotion_candidates"],
    }


def _snapshot(session: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    record = session.run(SNAPSHOT_CYPHER).single()
    if record is None:
        return [], []
    nodes = [dict(item) for item in record["nodes"] if item.get("id") is not None]
    edges = [dict(item) for item in record["edges"] if item.get("from") is not None]
    return nodes, edges


def _counts(session: Any) -> dict[str, int]:
    record = session.run(COUNT_CYPHER).single()
    if record is None:
        return {"nodes": 0, "edges": 0}
    return {"nodes": int(record["nodes"]), "edges": int(record["edges"])}


def load_graph(driver: Any, graph: Mapping[str, Any], mode: str = "full") -> dict[str, Any]:
    """Validate and load a graph, returning counts and review-only orphan data."""
    verify_graph(graph)
    with driver.session() as session:
        existing_nodes, existing_edges = _snapshot(session)
        before = _counts(session)
        plan = calculate_load_plan(graph, existing_nodes, existing_edges, mode)
        if plan["nodes"]:
            session.run(NODE_CYPHER, nodes=plan["nodes"]).consume()
        if plan["edges"]:
            session.run(EDGE_CYPHER, edges=plan["edges"]).consume()
        after = _counts(session)

    return {
        "mode": mode,
        "before": before,
        "after": after,
        "delta": {"nodes": after["nodes"] - before["nodes"], "edges": after["edges"] - before["edges"]},
        "merged": {"nodes": len(plan["nodes"]), "edges": len(plan["edges"])},
        "orphaned": plan["orphaned"],
        "promotion_candidates": plan["promotion_candidates"],
    }


def credentials_from_env(path: str | os.PathLike[str] = "~/.rpbd/neo4j.env") -> tuple[str, str]:
    """Read NEO4J_AUTH from a local mode-0600 environment file."""
    env_path = Path(path).expanduser()
    if not env_path.is_file():
        raise FileNotFoundError(
            f"{env_path} 파일을 만들고 NEO4J_AUTH=neo4j/<강력한-비밀번호>를 설정한 뒤 chmod 600을 적용하세요."
        )
    if stat.S_IMODE(env_path.stat().st_mode) != 0o600:
        raise PermissionError(f"{env_path} permissions must be 0600")
    auth_value = ""
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("NEO4J_AUTH="):
            auth_value = line.split("=", 1)[1].strip().strip("\"'")
            break
    if "/" not in auth_value:
        raise ValueError(f"{env_path} must contain NEO4J_AUTH=<username>/<password>")
    return tuple(auth_value.split("/", 1))  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load graph.json into local Neo4j")
    parser.add_argument("graph_json", type=Path)
    parser.add_argument("--mode", choices=("full", "incremental"), default="full")
    parser.add_argument("--uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--env-file", default="~/.rpbd/neo4j.env")
    args = parser.parse_args(argv)

    from neo4j import GraphDatabase

    username, password = credentials_from_env(args.env_file)
    graph = read_graph(args.graph_json)
    with GraphDatabase.driver(args.uri, auth=(username, password)) as driver:
        report = load_graph(driver, graph, args.mode)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
