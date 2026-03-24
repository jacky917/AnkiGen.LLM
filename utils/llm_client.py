"""
LLM 客戶端 (LLM Client) 模組。

職責：
1. 封裝 AsyncOpenAI 客戶端與 LLM (例如相容 OpenAI API 格式的 Gemini) 交互。
2. 透過 Response Format (JSON Schema) 強制保證輸出 100% 格式。
"""

import logging
import json
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from typing import Dict

from utils.config_manager import config


class LLMClient:
    """封裝與 LLM (相容 OpenAI 格式) 相關操作的類別。

    Attributes:
        _client (AsyncOpenAI): 非同步的 OpenAI API 客戶端。
        _model_name (str): LLM 的模型名稱。
        _logger (logging.Logger): 本地日誌記錄器。
    """

    def __init__(self) -> None:
        """根據 ConfigManager 的設定初始化 AsyncOpenAI 客戶端。"""
        self._logger = logging.getLogger(__name__)
        self._client = AsyncOpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            # 可以根據需求設定超時處理，這裡依賴其內部預設超時
        )
        self._model_name = config.llm_model_name
        self._logger.info("LLMClient 初始化完成，目標模型: %s", self._model_name)

    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Dict[str, object]
    ) -> Dict[str, object]:
        """呼叫 LLM 並取得嚴格符合 response_schema 的 JSON 資料。

        利用 OpenAI API 的 `response_format` 功能保證輸出為合規的 JSON。

        Args:
            system_prompt (str): 指定給 LLM 的系統提示，規範其扮演角色與注意事項。
            user_prompt (str): 使用者的輸入內容，例如要被製成卡片的原文。
            response_schema (Dict[str, object]): JSON Schema 定義。這會用於限制 LLM 的返回值必須為特定欄位。

        Returns:
            Dict[str, object]: 將 LLM 字串輸出反序列化後的 Python 字典。

        Raises:
            Exception: 發送請求失敗、逾時、或返回值並非有效 JSON 時拋出。
        """
        self._logger.debug("嘗試發送結構化生成請求...")

        # 建構供 openai 所需的 structured JSON schema 格式
        # 這是為了配合 Structured Outputs Beta
        structured_format: Dict[str, object] = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "schema": response_schema,
                "strict": True
            }
        }

        try:
            response: ChatCompletion = await self._client.chat.completions.create(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                # 傳遞嚴格的 JSON Schema
                response_format=structured_format,
                temperature=0.0  # 設為 0 以追求最大的格式穩定性與減少幻覺
            )
        except Exception as e:
            self._logger.error("LLM API 請求失敗: %s", str(e))
            raise

        response_content = response.choices[0].message.content

        if not response_content:
            self._logger.error("LLM API 回傳內容為空。")
            raise ValueError("LLM API 返回結果為空")

        try:
            # 將回傳結果反序列化為字典回傳
            parsed_data: Dict[str, object] = json.loads(response_content)
            self._logger.debug("LLM 回傳 JSON 成功解析。")
            return parsed_data
        except json.JSONDecodeError as decode_error:
            self._logger.error("無法將 LLM API 回傳結果解析為 JSON。原始文字: %s", response_content)
            raise ValueError(f"LLM 輸出非有效 JSON 格式: {decode_error}")
