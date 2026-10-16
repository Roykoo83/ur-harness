"""Attribute a submitted document to a project.

People will not tag consistently -- that is the design premise, not a failure to
correct. Forcing a tag makes people skip saving altogether, and then the data
never arrives. So tagging is cheap (three lines), untagged work is recovered
rather than dropped, and whatever cannot be resolved surfaces on the decision
board instead of disappearing.

Four steps, in order of trust:
  1. frontmatter `project`   -> ledger    (a person said so)
  2. filename pattern        -> ledger    (a person said so)
  3. body match              -> inferred  (a machine guessed)
  4. nothing                 -> unresolved

`project: 미상` is kept distinct from an absent tag: both need a human, but one
means "I looked and could not tell" and the other means "I never said."
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

# Explicit "I don't know" values a person may write.
UNKNOWN_TOKENS = frozenset({"미상", "unknown", "?", "-", "미정"})

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)
# A trailing \b would fail on "PRJ-N003_20260804.md", since _ is a word character
# and the most common filename convention puts one right after the id.
_PROJECT_ID = re.compile(r"\bPRJ-[A-Z]?\d{3,}(?![0-9])", re.I)


@dataclass(frozen=True)
class ProjectAlias:
    """One project and the strings that identify it."""

    project_id: str
    names: tuple[str, ...]


@dataclass
class Attribution:
    project_id: str | None
    provenance: str          # ledger | inferred | unresolved
    method: str              # frontmatter | filename | body | declared_unknown | none
    matched_on: str | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self.provenance != "ledger"


def parse_frontmatter(text: str) -> dict[str, str]:
    """Read the leading `---` block. Values are plain scalars; lists are ignored."""
    match = _FRONTMATTER.match(text or "")
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip().strip("\"'")
    return fields


def build_aliases(projects: Iterable[Mapping[str, object]]) -> list[ProjectAlias]:
    """Derive match strings from SSOT project records rather than hardcoding them.

    Short aliases are what people actually type ("팁스 글로벌트랙"), so they matter
    more than official titles. Anything under three characters is dropped -- a
    two-character token matches far too much prose to be evidence of anything.
    """
    aliases: list[ProjectAlias] = []
    for payload in projects:
        project_id = str(payload.get("project_id") or "").strip()
        if not project_id:
            continue
        names = {
            str(payload.get(key) or "").strip()
            for key in ("name_short", "name_official", "folder_path")
        }
        cleaned = tuple(
            sorted(
                {re.sub(r"^\d+\.\s*", "", n) for n in names if len(n) >= 3},
                key=len,
                reverse=True,
            )
        )
        if cleaned:
            aliases.append(ProjectAlias(project_id=project_id, names=cleaned))
    return aliases


def _match_alias(haystack: str, aliases: Iterable[ProjectAlias]) -> tuple[str, str] | None:
    lowered = (haystack or "").lower()
    for alias in aliases:
        for name in alias.names:
            if name.lower() in lowered:
                return alias.project_id, name
    return None


def attribute(
    *,
    filename: str,
    text: str | None,
    aliases: Iterable[ProjectAlias],
) -> Attribution:
    """Resolve which project a submission belongs to."""
    aliases = list(aliases)
    known_ids = {a.project_id.upper() for a in aliases}
    body = text or ""

    # 1. The author said so, in frontmatter.
    declared = parse_frontmatter(body).get("project", "").strip()
    if declared:
        if declared.lower() in UNKNOWN_TOKENS or declared in UNKNOWN_TOKENS:
            return Attribution(None, "unresolved", "declared_unknown")
        found = _PROJECT_ID.search(declared)
        if found and found.group(0).upper() in known_ids:
            return Attribution(found.group(0).upper(), "ledger", "frontmatter", declared)
        hit = _match_alias(declared, aliases)
        if hit:
            return Attribution(hit[0], "ledger", "frontmatter", hit[1])
        # Declared but unrecognised: trust the intent, flag the value.
        return Attribution(None, "unresolved", "frontmatter", declared)

    # 2. The author said so, in the filename.
    found = _PROJECT_ID.search(filename or "")
    if found and found.group(0).upper() in known_ids:
        return Attribution(found.group(0).upper(), "ledger", "filename", found.group(0))
    hit = _match_alias(filename or "", aliases)
    if hit:
        return Attribution(hit[0], "ledger", "filename", hit[1])

    # 3. A machine guessed from the body.
    hit = _match_alias(body, aliases)
    if hit:
        return Attribution(hit[0], "inferred", "body", hit[1])

    # 4. Nothing. Never dropped -- it goes to the review queue.
    return Attribution(None, "unresolved", "none")


__all__ = [
    "UNKNOWN_TOKENS",
    "Attribution",
    "ProjectAlias",
    "attribute",
    "build_aliases",
    "parse_frontmatter",
]
