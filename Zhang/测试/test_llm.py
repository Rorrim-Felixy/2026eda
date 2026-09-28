import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


load_dotenv()

api_key = os.getenv("DASHSCOPE_API_KEY")
base_url = os.getenv("DASHSCOPE_BASE_URL")
model_name = os.getenv("DASHSCOPE_MODEL", "qwen-plus")

if not api_key:
    raise RuntimeError("没有读取到 DASHSCOPE_API_KEY，请检查 .env 文件")

if not base_url:
    raise RuntimeError("没有读取到 DASHSCOPE_BASE_URL，请检查 .env 文件")

llm = ChatOpenAI(
    model=model_name,
    api_key=api_key,
    base_url=base_url,
    temperature=0,
)

response = llm.invoke(
    [
        (
            "system",
            "你是GaN HEMT紧凑模型参数提取助手。"
            "请根据拟合误差提出清晰、简短且物理合理的建议。",
        ),
        (
            "user",
            "现在只是连接测试。请回复：LLM连接成功。",
        ),
    ]
)

print("模型名称：", model_name)
print("模型回复：", response.content)