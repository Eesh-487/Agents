"""Checks the deterministic post-processing of the section-by-section build -
no model, no Neo4j. Run from Agents/: .venv/bin/python -m set1_graph_builder.test_graph_builder"""
from set1_graph_builder.graph_builder import (
    _attach_orphans,
    _clean_entities,
    _drop_invalid_relationships,
    _merge_section,
    _split_sections,
    _synthesize_owner_relationships,
)
from set1_graph_builder.structural_rules import check_graph_structure


def _e(id, type, name, owner_id=None):
    return {"id": id, "type": type, "name": name, "owner_id": owner_id}


def _r(s, t, type):
    return {"source_id": s, "target_id": t, "type": type}


def test():
    header, sections = _split_sections("Title Doc\n\n1. PURPOSE AND SCOPE\n\ntext\n\n3.1 sub\n\n2. DATA RETENTION\n\n4.1 more")
    assert header == "Title Doc"
    assert [s.splitlines()[0] for s in sections] == ["1. PURPOSE AND SCOPE", "2. DATA RETENTION"]

    entities, relationships = [], []
    _merge_section(entities, relationships,
                   [_e("policy-a", "Policy", "Privacy Policy"), _e("department-compliance", "Department", "Compliance Department")],
                   [_r("department-compliance", "policy-a", "OWNS")])
    # Later section: same department by name, a second Policy, the same concept under two types,
    # a reversed OWNS, a MITIGATES pointing at a non-Risk, and an orphan Risk.
    _merge_section(entities, relationships,
                   [_e("department-compliance-team", "Department", "Compliance"),
                    _e("policy-b", "Policy", "Data Policy"),
                    _e("control-dsr", "Control", "DSR Handling", owner_id="department-compliance-team"),
                    _e("process-dsr", "Process", "DSR Handling"),
                    _e("requirement-30-days", "Requirement", "Respond in 30 days"),
                    _e("risk-breach", "Risk", "Data breach")],
                   [_r("control-dsr", "department-compliance-team", "OWNS"),
                    _r("process-dsr", "requirement-30-days", "SATISFIES"),
                    _r("control-dsr", "requirement-30-days", "MITIGATES"),
                    _r("policy-b", "requirement-30-days", "REQUIRES")])

    ids = {e["id"] for e in entities}
    assert ids == {"policy-a", "department-compliance", "control-dsr", "requirement-30-days", "risk-breach"}, ids
    assert next(e for e in entities if e["id"] == "control-dsr")["owner_id"] == "department-compliance"

    entities = _clean_entities(entities)
    relationships = _synthesize_owner_relationships(entities, relationships)
    relationships = _drop_invalid_relationships(entities, relationships)
    relationships = _attach_orphans(entities, relationships)
    edges = {(r["source_id"], r["target_id"], r["type"]) for r in relationships}
    assert edges == {
        ("department-compliance", "policy-a", "OWNS"),
        ("department-compliance", "control-dsr", "OWNS"),  # reversed edge swapped, deduped against synthesized one
        ("control-dsr", "requirement-30-days", "SATISFIES"),
        ("policy-a", "requirement-30-days", "REQUIRES"),
        ("policy-a", "risk-breach", "RELATES_TO"),  # orphan attached; bad MITIGATES dropped
    }, edges
    assert len(relationships) == len(edges)
    assert check_graph_structure(entities, relationships)[0]


if __name__ == "__main__":
    test()
    print("ok")
