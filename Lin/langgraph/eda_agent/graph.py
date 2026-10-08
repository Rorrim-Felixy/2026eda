from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from eda_agent.models import ModelPair
from eda_agent.nodes import AgentNodes
from eda_agent.state import AgentState
from eda_agent.tools import PMMSClient


def build_graph(
    models: ModelPair,
    pmms: PMMSClient | None = None,
    approval_score: float = 8.0,
    blind_mode: bool = True,
):
    """Build and compile the primary -> tool -> evaluator review loop."""
    nodes = AgentNodes(
        models=models,
        pmms=pmms,
        approval_score=approval_score,
        blind_mode=blind_mode,
    )

    builder = StateGraph(AgentState)
    builder.add_node("primary", nodes.primary)
    builder.add_node("meqlab", nodes.meqlab)
    builder.add_node("tool_limit", nodes.tool_limit)
    builder.add_node("evaluator", nodes.evaluator)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "primary")
    builder.add_conditional_edges("primary", nodes.after_primary)
    # Return tool output to the primary model before evaluation.  Otherwise the
    # final answer would be the proposal written before MeQLab returned data.
    builder.add_edge("meqlab", "primary")
    builder.add_edge("tool_limit", "evaluator")
    builder.add_conditional_edges("evaluator", nodes.after_evaluator)
    builder.add_edge("finalize", END)
    return builder.compile()
