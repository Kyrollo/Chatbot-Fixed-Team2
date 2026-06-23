"""
Ontology Definition — Task 0.1 (Expanded)

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
(see migrations/sprint4_ontology_expansion.sql) — the two must stay
in sync manually, since AGE does not enforce label sets itself.

EXPANDED: Phase 4 of the implementation plan adds domain-relevant
entity types for documents, forms, agencies, regulations, etc. and
corresponding relation types for document/legal/business domains.
"""
from enum import Enum


class EntityType(str, Enum):
    # Original entity types
    PERSON = "Person"
    PROJECT = "Project"
    DEPARTMENT = "Department"
    POLICY = "Policy"
    ROLE = "Role"
    LOCATION = "Location"
    SKILL = "Skill"
    # Phase 4 expansion: domain-relevant entity types
    DOCUMENT = "Document"
    FORM = "Form"
    ORGANIZATION = "Organization"
    AGENCY = "Agency"
    REGULATION = "Regulation"
    TAX_TERM = "TaxTerm"
    DATE = "Date"
    IDENTIFIER = "Identifier"
    REQUIREMENT = "Requirement"
    PROCEDURE = "Procedure"


class RelationType(str, Enum):
    # Original relation types
    MANAGES = "MANAGES"
    BELONGS_TO = "BELONGS_TO"
    REPORTS_TO = "REPORTS_TO"
    OWNS = "OWNS"
    HAS_ROLE = "HAS_ROLE"
    WORKS_ON = "WORKS_ON"
    HAS_SKILL = "HAS_SKILL"
    BASED_AT = "BASED_AT"
    # Phase 4 expansion: domain-relevant relation types
    DEFINED_BY = "DEFINED_BY"
    REQUIRES = "REQUIRES"
    APPLIES_TO = "APPLIES_TO"
    PART_OF = "PART_OF"
    REFERENCES = "REFERENCES"
    ISSUED_BY = "ISSUED_BY"
    HAS_SECTION = "HAS_SECTION"


ENTITY_TYPES: list[str] = [e.value for e in EntityType]
RELATION_TYPES: list[str] = [r.value for r in RelationType]


def is_valid_entity_type(label: str) -> bool:
    return label in ENTITY_TYPES


def is_valid_relation_type(label: str) -> bool:
    return label in RELATION_TYPES
