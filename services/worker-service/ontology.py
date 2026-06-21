"""
Ontology Definition — Task 0.1

Single source of truth for every entity type and relation type allowed
in the knowledge graph. Every other Sprint 3 service imports from here
instead of hardcoding label strings:

  - ner-service        -> restricts extraction to ENTITY_TYPES
  - relation-extraction -> restricts output to RELATION_TYPES
  - graph_writer.py    -> rejects any label not in this file
  - retrieval-service  -> graph_retriever.py validates query-time
                           entity types against this same list

Adding a new entity or relation type means editing this file AND
running a migration to create the matching vlabel/elabel in AGE
(see migrations/sprint3_foundation.sql) — the two must stay in sync
manually, since AGE does not enforce label sets itself.
"""
from enum import Enum


class EntityType(str, Enum):
    PERSON = "Person"
    PROJECT = "Project"
    DEPARTMENT = "Department"
    POLICY = "Policy"
    ROLE = "Role"
    LOCATION = "Location"
    SKILL = "Skill"


class RelationType(str, Enum):
    MANAGES = "MANAGES"
    BELONGS_TO = "BELONGS_TO"
    REPORTS_TO = "REPORTS_TO"
    OWNS = "OWNS"
    HAS_ROLE = "HAS_ROLE"
    WORKS_ON = "WORKS_ON"
    HAS_SKILL = "HAS_SKILL"
    BASED_AT = "BASED_AT"


ENTITY_TYPES: list[str] = [e.value for e in EntityType]
RELATION_TYPES: list[str] = [r.value for r in RelationType]


def is_valid_entity_type(label: str) -> bool:
    return label in ENTITY_TYPES


def is_valid_relation_type(label: str) -> bool:
    return label in RELATION_TYPES
