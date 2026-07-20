from typing import Any


class DeepSeekGateway:
    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    async def answer_test(self, prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你正在执行 API 连通性测试。只回答用户给出的测试问题，"
                        "AI测试不代表任何平台政策结论。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            stream=False,
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError("DeepSeek returned an empty response")
        return content.strip()
