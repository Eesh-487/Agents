"""Set 3: Gap Analysis. Two independent agents compare the company's Neo4j
graph against ingested law text from opposite directions, and a third agent
merges their findings into one final, deduplicated gap list - gated by a
deterministic structural check (gap_rules.py) as the source of truth, same
principle as Set 1.

    company_to_law ─┐
                     ├──► merge ──► rules_check ──► done / retry merge / escalate
    law_to_company ──┘

Company -> Law catches *insufficient* existing controls. Law -> Company
catches controls that are *missing entirely* - critical here, since the
NimbusPay policy's known gaps (no breach notification process, no
cross-border transfer restriction, no named DPO) are all absences with no
graph entity to start from, which Company -> Law alone would never surface.
"""
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import graph_db
import vector_store
from json_utils import call_agent_for_json
from llm import DISCRIMINATOR_MODEL, chatbot
from Prompts.company_to_law import COMPANY_TO_LAW_SYSTEM_PROMPT, COMPANY_TO_LAW_USER_TEMPLATE
from Prompts.gap_merge import GAP_MERGE_SYSTEM_PROMPT, GAP_MERGE_USER_TEMPLATE
from Prompts.law_to_company import LAW_TO_COMPANY_SYSTEM_PROMPT, LAW_TO_COMPANY_USER_TEMPLATE
from retrieval import hybrid_search
from set3_gap_analysis.gap_rules import check_gap_list

COMPANY_ENTITY_TYPES = {"Control", "Policy", "Process"}
LAW_SECTION_BATCH_SIZE = 3
MAX_MERGE_ATTEMPTS = 3


class GapAnalysisState(TypedDict, total=False):
    law_collection: str
    cancel_event: object
    graph_entities: list
    graph_relationships: list
    company_to_law_gaps: list
    law_to_company_gaps: list
    law_to_company_failed_batches: int
    law_to_company_total_batches: int
    final_gaps: list
    merge_feedback: str
    merge_attempts: int
    merge_error: str | None
    rules_passed: bool
    rules_explanation: str


def _entity_relationships(entity_id, relationships):
    return [r for r in relationships if r["source_id"] == entity_id or r["target_id"] == entity_id]


def company_to_law_node(state: GapAnalysisState) -> dict:
    """Agent 1: for each existing Control/Policy/Process, retrieve the most
    relevant law text and check whether it's actually satisfied."""
    relevant_entities = [e for e in state["graph_entities"] if e.get("type") in COMPANY_ENTITY_TYPES]
    gaps = []

    for entity in relevant_entities:
        query = f"{entity.get('name', '')}. {entity.get('description', '')}"
        results = hybrid_search(query, state["law_collection"], top_k=5)
        retrieved_text = "\n\n".join(
            f"[Section {r['metadata'].get('section', '?')}]\n{r['document']}" for r in results
        )
        if not retrieved_text:
            continue

        agent = chatbot(system=COMPANY_TO_LAW_SYSTEM_PROMPT, temperature=0.2)
        prompt = COMPANY_TO_LAW_USER_TEMPLATE.format(
            entity_json=json.dumps(entity, indent=2),
            entity_relationships_json=json.dumps(_entity_relationships(entity["id"], state["graph_relationships"]), indent=2),
            retrieved_law_chunks=retrieved_text,
        )
        verdict, error = call_agent_for_json(agent, prompt, cancel_event=state.get("cancel_event"))
        if error is not None:
            print(f"[company_to_law] entity '{entity['id']}' skipped after error: {error}")
            continue

        if verdict.get("has_gap"):
            gaps.append(
                {
                    "entity_id": entity["id"],
                    "gap_description": verdict.get("gap_description", ""),
                    "severity": verdict.get("severity", "medium"),
                    "cited_law_sections": verdict.get("cited_law_sections", []),
                    "recommendation": verdict.get("recommendation", ""),
                }
            )

    print(f"[company_to_law] checked {len(relevant_entities)} entities, found {len(gaps)} gaps")
    return {"company_to_law_gaps": gaps}


def law_to_company_node(state: GapAnalysisState) -> dict:
    """Agent 2: for each batch of law sections, check whether the (small,
    fully-in-context) company graph has any control addressing it at all."""
    all_chunks = vector_store.get_all_chunks(state["law_collection"])
    entities_json = json.dumps(state["graph_entities"], indent=2)
    relationships_json = json.dumps(state["graph_relationships"], indent=2)

    gaps = []
    batch_count = 0
    failed_batches = 0
    for i in range(0, len(all_chunks), LAW_SECTION_BATCH_SIZE):
        batch_count += 1
        batch = all_chunks[i : i + LAW_SECTION_BATCH_SIZE]
        batch_text = "\n\n".join(f"[Section {meta.get('section', '?')}]\n{doc}" for _, doc, meta in batch)

        # Generator-tier model, not the discriminator - this is a comparison/
        # generation task ("2 agents compare"), same as company_to_law. Only
        # the merge step below is the actual discriminator ("3rd discriminates").
        # Small batch size + this model's low per-minute token cap (6000 TPM
        # on Groq's free tier) don't leave much room - keep batches small.
        agent = chatbot(system=LAW_TO_COMPANY_SYSTEM_PROMPT, temperature=0.2, max_tokens=1500)
        prompt = LAW_TO_COMPANY_USER_TEMPLATE.format(
            law_sections_batch=batch_text,
            all_entities_json=entities_json,
            all_relationships_json=relationships_json,
        )
        result, error = call_agent_for_json(agent, prompt, cancel_event=state.get("cancel_event"))
        if error is not None:
            failed_batches += 1
            print(f"[law_to_company] batch {i} skipped after error: {error}")
        else:
            gaps.extend(result.get("gaps", []))

    print(f"[law_to_company] checked {len(all_chunks)} sections in {batch_count} batches "
          f"({failed_batches} failed), found {len(gaps)} gaps")
    return {"law_to_company_gaps": gaps, "law_to_company_failed_batches": failed_batches, "law_to_company_total_batches": batch_count}


def _condense_gap_for_merge(gap):
    """The merge agent only needs enough to deduplicate and prioritize - it
    writes its own final descriptions/recommendations, it doesn't need to
    preserve the source text verbatim. With real findings running into the
    dozens (a 43-section Act vs. a real company graph genuinely produces
    this many candidates), passing full verbose objects into one merge call
    blows past Groq's free-tier per-minute token cap; this cuts the input
    size to what's actually needed."""
    return {
        "law_section": gap.get("law_section") or (gap.get("cited_law_sections") or [None])[0],
        "entity_id": gap.get("entity_id"),
        "related_entity_ids": gap.get("related_entity_ids", []),
        "severity": gap.get("severity"),
        "gap_description": (gap.get("gap_description") or "")[:200],
    }


def merge_node(state: GapAnalysisState) -> dict:
    """Agent 3: merges both gap lists into one deduplicated, prioritized
    list. Re-run (with feedback) if the rules check rejects its output.

    On failure (parse error, API error), returns an explicit merge_error
    rather than an empty final_gaps list - an API failure must never be
    indistinguishable from "the analysis genuinely found zero gaps"."""
    condensed_company_to_law = [_condense_gap_for_merge(g) for g in state.get("company_to_law_gaps", [])]
    condensed_law_to_company = [_condense_gap_for_merge(g) for g in state.get("law_to_company_gaps", [])]

    agent = chatbot(system=GAP_MERGE_SYSTEM_PROMPT, model=DISCRIMINATOR_MODEL, temperature=0.0, max_tokens=3000)
    prompt = GAP_MERGE_USER_TEMPLATE.format(
        company_to_law_gaps_json=json.dumps(condensed_company_to_law, indent=2),
        law_to_company_gaps_json=json.dumps(condensed_law_to_company, indent=2),
    )
    if state.get("merge_feedback"):
        prompt += f"\n\nPrevious merge attempt was rejected for this reason - fix it: {state['merge_feedback']}"

    result, error = call_agent_for_json(agent, prompt, cancel_event=state.get("cancel_event"))
    if error is not None:
        print(f"[merge] failed: {error}")
        return {"final_gaps": [], "merge_attempts": state.get("merge_attempts", 0) + 1, "merge_error": error}

    return {
        "final_gaps": result.get("final_gaps", []),
        "merge_attempts": state.get("merge_attempts", 0) + 1,
        "merge_error": None,
    }


def rules_check_node(state: GapAnalysisState) -> dict:
    """Deterministic gate - the source of truth. Catches hallucinated law
    section citations or entity references before they reach a human.

    If the merge step itself failed, skip structural validation entirely and
    route straight to retry/escalate - an empty list from a failed merge
    must never be validated as "0 gaps found, all good"."""
    if state.get("merge_error"):
        explanation = f"Merge step failed: {state['merge_error']}"
        print(f"[rules_check] SKIPPED (merge failed) - {explanation}")
        return {"rules_passed": False, "rules_explanation": explanation, "merge_feedback": explanation}

    all_chunks = vector_store.get_all_chunks(state["law_collection"])
    valid_law_sections = {meta.get("section") for _, _, meta in all_chunks}
    valid_entity_ids = {e["id"] for e in state["graph_entities"]}

    passed, explanation = check_gap_list(state["final_gaps"], valid_law_sections, valid_entity_ids)
    print(f"[rules_check] {'PASSED' if passed else 'FAILED'} - {explanation}")
    return {"rules_passed": passed, "rules_explanation": explanation, "merge_feedback": explanation if not passed else ""}


def _route_after_rules_check(state: GapAnalysisState) -> str:
    if state["rules_passed"]:
        return "done"
    if state.get("merge_attempts", 0) >= MAX_MERGE_ATTEMPTS:
        return "escalate"
    return "retry"


def _build_graph():
    builder = StateGraph(GapAnalysisState)
    builder.add_node("company_to_law", company_to_law_node)
    builder.add_node("law_to_company", law_to_company_node)
    builder.add_node("merge", merge_node)
    builder.add_node("rules_check", rules_check_node)

    builder.add_edge(START, "company_to_law")
    builder.add_edge(START, "law_to_company")
    builder.add_edge("company_to_law", "merge")
    builder.add_edge("law_to_company", "merge")
    builder.add_edge("merge", "rules_check")
    builder.add_conditional_edges(
        "rules_check", _route_after_rules_check, {"done": END, "escalate": END, "retry": "merge"}
    )
    return builder.compile()


_compiled_graph = _build_graph()


def run_gap_analysis(law_collection="dpdp_act_2023", cancel_event=None):
    graph_data = graph_db.get_full_graph()
    initial_state: GapAnalysisState = {
        "law_collection": law_collection,
        "cancel_event": cancel_event,
        "graph_entities": graph_data["entities"],
        "graph_relationships": graph_data["relationships"],
        "company_to_law_gaps": [],
        "law_to_company_gaps": [],
        "law_to_company_failed_batches": 0,
        "law_to_company_total_batches": 0,
        "final_gaps": [],
        "merge_feedback": "",
        "merge_attempts": 0,
        "merge_error": None,
        "rules_passed": False,
        "rules_explanation": "",
    }
    result = _compiled_graph.invoke(initial_state)

    failed_batches = result.get("law_to_company_failed_batches", 0)
    status = "completed" if result["rules_passed"] else "escalated"
    if failed_batches > 0 and status == "completed":
        # Ran to completion, but part of the analysis was silently incomplete -
        # a caller checking only `status` must not mistake this for a clean run.
        status = "completed_with_warnings"

    return {
        "status": status,
        "gap_count": len(result["final_gaps"]),
        "final_gaps": result["final_gaps"],
        "company_to_law_gap_count": len(result["company_to_law_gaps"]),
        "law_to_company_gap_count": len(result["law_to_company_gaps"]),
        "law_to_company_batches_failed": f"{failed_batches}/{result.get('law_to_company_total_batches', 0)}",
        "rules_explanation": result["rules_explanation"],
    }


if __name__ == "__main__":
    print(json.dumps(run_gap_analysis(), indent=2))
