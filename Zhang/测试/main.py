from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class AgentState(TypedDict):
    message: str


def check_environment(state: AgentState) -> AgentState:
    print("正在执行：检查开发环境")
    return {"message": state["message"] + "Python环境正常；"}


def check_langgraph(state: AgentState) -> AgentState:
    print("正在执行：检查LangGraph")
    return {"message": state["message"] + "LangGraph运行正常。"}


builder = StateGraph(AgentState)

builder.add_node("check_environment", check_environment)
builder.add_node("check_langgraph", check_langgraph)

builder.add_edge(START, "check_environment")
builder.add_edge("check_environment", "check_langgraph")
builder.add_edge("check_langgraph", END)

agent = builder.compile()

result = agent.invoke({"message": ""})

print("运行结果：", result["message"])