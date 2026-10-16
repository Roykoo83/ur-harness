"""C4.5 read queries using Neo4j class properties rather than labels."""

from __future__ import annotations

from typing import Any

PROJECT_AGENCIES = """
MATCH (p {class: 'GovProject', businessKey: $pid})-[:fundedBy]->(a)
RETURN DISTINCT a.id AS agency_id
ORDER BY agency_id
"""
PERSON_PROJECTS = """
MATCH (n {id: $person})-[r:managesProject|allocatedTo]->(p {class: 'GovProject'})
RETURN DISTINCT p.id AS project_id, type(r) AS relation
ORDER BY project_id, relation
"""
SHARED_PARTNERS = """
MATCH (o)-[:participatesIn]->(p1 {class: 'GovProject', businessKey: $a}),
      (o)-[:participatesIn]->(p2 {class: 'GovProject', businessKey: $b})
RETURN DISTINCT o.id AS organization_id
ORDER BY organization_id
"""
ORGANIZATION_PROJECTS = """
MATCH (o {id: $org})-[:participatesIn]->(p {class: 'GovProject'})
RETURN p.id AS project_id
UNION
MATCH (p {class: 'GovProject'})-[:fundedBy|settledBy]->(o {id: $org})
RETURN p.id AS project_id
"""


def _data(result: Any) -> list[dict[str, Any]]:
    return [dict(record) for record in result]


def project_agencies(session: Any, project_business_key: str) -> list[dict[str, Any]]:
    """Return agencies funding a project."""
    return _data(session.run(PROJECT_AGENCIES, pid=project_business_key))


def person_projects(session: Any, person_id: str) -> list[dict[str, Any]]:
    """Return every project managed by or allocated to a person."""
    return _data(session.run(PERSON_PROJECTS, person=person_id))


def shared_partners(session: Any, project_a: str, project_b: str) -> list[dict[str, Any]]:
    """Return organizations participating in both projects."""
    return _data(session.run(SHARED_PARTNERS, a=project_a, b=project_b))


def organization_projects(session: Any, organization_id: str) -> list[dict[str, Any]]:
    """Return projects in which an organization participates or acts as an agency."""
    return _data(session.run(ORGANIZATION_PROJECTS, org=organization_id))

