import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

_driver = None
_DATABASE = os.getenv("NEO4J_DATABASE") or None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
        )
    return _driver


def close_driver():
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def run_write(query, params=None):
    driver = get_driver()
    with driver.session(database=_DATABASE) as session:
        return list(session.execute_write(lambda tx: list(tx.run(query, params or {}))))


def run_read(query, params=None):
    driver = get_driver()
    with driver.session(database=_DATABASE) as session:
        return list(session.execute_read(lambda tx: list(tx.run(query, params or {}))))


def clear_graph():
    """Wipes all nodes/relationships. Simple demo behavior: each build starts clean
    rather than merging against whatever was there before."""
    run_write("MATCH (n) DETACH DELETE n")


def upsert_entities(entities):
    for entity in entities:
        run_write(
            """
            MERGE (e:Entity {id: $id})
            SET e.type = $type,
                e.name = $name,
                e.description = $description,
                e.source_excerpt = $source_excerpt,
                e.owner_id = $owner_id
            """,
            {
                "id": entity["id"],
                "type": entity["type"],
                "name": entity["name"],
                "description": entity.get("description", ""),
                "source_excerpt": entity.get("source_excerpt", ""),
                "owner_id": entity.get("owner_id"),
            },
        )


def get_all_entities():
    rows = run_read(
        "MATCH (e:Entity) RETURN e.id AS id, e.type AS type, e.name AS name, "
        "e.description AS description, e.source_excerpt AS source_excerpt, e.owner_id AS owner_id"
    )
    return [dict(row) for row in rows]


def get_all_relationships():
    rows = run_read(
        "MATCH (s:Entity)-[r:RELATION]->(t:Entity) "
        "RETURN s.id AS source_id, t.id AS target_id, r.type AS type, r.description AS description"
    )
    return [dict(row) for row in rows]


def get_full_graph():
    """Set 3 reads the whole graph as context rather than querying it
    incrementally - at NimbusPay's scale (~13 entities) this is simpler and
    cheaper than building a second retrieval index just for graph entities.
    Revisit if the graph grows into the hundreds/thousands of entities."""
    return {"entities": get_all_entities(), "relationships": get_all_relationships()}


def upsert_relationships(relationships):
    # Generic :RELATION label with a `type` property, rather than dynamically
    # naming the Cypher relationship type from LLM output - Neo4j can't
    # parameterize label/rel-type names, and string-building them in would
    # mean interpolating LLM-controlled text straight into the query.
    for rel in relationships:
        run_write(
            """
            MATCH (source:Entity {id: $source_id})
            MATCH (target:Entity {id: $target_id})
            MERGE (source)-[r:RELATION {type: $type}]->(target)
            SET r.description = $description
            """,
            {
                "source_id": rel["source_id"],
                "target_id": rel["target_id"],
                "type": rel["type"],
                "description": rel.get("description", ""),
            },
        )
