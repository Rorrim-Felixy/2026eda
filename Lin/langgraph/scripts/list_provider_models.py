from __future__ import annotations

import asyncio

from openai import AsyncOpenAI

from eda_agent.config import Settings


async def main() -> None:
    settings = Settings.load(require_models=True)
    client = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
    page = await client.models.list()
    names = sorted(
        model.id
        for model in page.data
        if "kimi" in model.id.lower() or "deepseek" in model.id.lower()
    )
    print("\n".join(names))


if __name__ == "__main__":
    asyncio.run(main())
