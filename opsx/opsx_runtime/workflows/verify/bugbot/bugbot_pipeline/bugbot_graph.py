"""One real LangGraph node: Bugbot -> adapter call."""

from langgraph.graph import START, END, StateGraph

from agents.abc_agent import ABCAgent
from agents.state import GraphState


def build_bugbot_graph(agent: ABCAgent, *, checkpointer=None):
    builder = StateGraph(GraphState)
    builder.add_node("bugbot", agent.run)
    builder.add_edge(START, "bugbot")
    builder.add_edge("bugbot", END)
    return builder.compile(checkpointer=checkpointer)
