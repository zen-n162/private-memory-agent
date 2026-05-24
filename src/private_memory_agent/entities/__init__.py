"""Entity resolution and alias management."""

from private_memory_agent.entities.resolver import (
    AliasAddResult,
    EntityMention,
    EntityResolveResult,
    EntityResolver,
    add_entity_alias,
    list_entities,
    resolve_text_annotation_entities,
)

__all__ = [
    "AliasAddResult",
    "EntityMention",
    "EntityResolveResult",
    "EntityResolver",
    "add_entity_alias",
    "list_entities",
    "resolve_text_annotation_entities",
]
