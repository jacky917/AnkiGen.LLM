"""
自動化工作流總入口 (Main Workflow) 模組。

職責：
1. 作為進入點，接收使用者資料（卡片目標牌組、目標模型名稱、與使用者輸入原文）。
2. 調用 ConfigManager 讀取設定。
3. 利用 AnkiModelManager 解析模型定義的 JSON Schema。
4. 將 JSON Schema 與原文餵給 LLMClient，並強制傳回符合格式的 JSON。
5. 將拿到的 JSON 回傳給 AnkiModelManager，封裝並發送至 AnkiConnect。
"""

import asyncio
import logging
import sys
from typing import Dict

from utils.config_manager import config
from utils.llm_client import LLMClient
from utils.anki_model_manager import AnkiModelManager, AnkiActionContext


class MainWorkflow:
    """自動化生卡並發送至 Anki 的工作流編排類別。"""

    def __init__(self) -> None:
        """初始化工作流程依賴的各類別實例。"""
        self._logger = logging.getLogger(__name__)
        self._llm_client = LLMClient()
        self._anki_manager = AnkiModelManager()
        self._logger.info("工作流程依賴物件初始化完成。")

    async def execute(
        self,
        deck_name: str,
        model_name: str,
        user_prompt: str,
        model_schema_file: str,
        system_prompt: str = "請作為一個專業的語言學家，從使用者的輸入中整理並輸出符合格式的 JSON。不要加入任何額外的解釋文字。",
        tags: list[str] | None = None,
        auto_sync: bool = False
    ) -> int:
        """執行完整的工作流：從 LLM 生成到 AnkiConnect 提交。

        Args:
            deck_name (str): 目標存入的 Anki 牌組名稱。
            model_name (str): 在 Anki 裡實際的筆記模型名稱 (與 model_schema_file 的 definition 一致)。
            user_prompt (str): 使用者的素材，預計交給 LLM 解析的文本。
            model_schema_file (str): 對應於 ./anki_models/ 下的 JSON 模型定義檔 (例如: `vocabulary_model.json`)。
            system_prompt (str, optional): 引導 LLM 角色的 Prompt。預設提供基礎的翻譯角色設定。
            tags (list[str] | None, optional): 加到卡片上的標籤陣列。預設為 None。
            auto_sync (bool, optional): 是否在新增卡片後強制觸發與 AnkiWeb 的同步。預設為 False。

        Returns:
            int: 成功建立的 Anki 卡片 ID。

        Raises:
            Exception: 工作流中的任一方異常。
        """
        self._logger.info("=== 開始執行 LLM to Anki 自動化工作流 ===")
        self._logger.info("目標牌組: %s | 目標模型: %s", deck_name, model_name)

        try:
            # 1. 取得 JSON Schema (強制 LLM 的輸出格式)
            schema: Dict[str, object] = self._anki_manager.get_model_schema(model_schema_file)

            # 2. 發送給 LLM 取得生成資料
            self._logger.info("正在請求 LLM 生成結構化卡片內容...")
            structured_data: Dict[str, object] = await self._llm_client.generate_structured_data(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=schema
            )
            self._logger.debug("取得之結構化資料: %s", structured_data)

            # 3. 組裝 Anki 動作
            action_context: AnkiActionContext = self._anki_manager.create_add_note_action(
                deck_name=deck_name,
                model_name=model_name,
                llm_response=structured_data,
                tags=tags or ["LLM_Auto_Generated"]
            )

            # 4. 提交至本地 Anki (透過 httpx 與 AnkiConnect 通訊)
            note_id: int = await self._anki_manager.submit_action(action_context)
            
            # 5. 若開啟自動同步，則觸發與 AnkiWeb 同步
            if auto_sync:
                await self._anki_manager.sync_to_ankiweb()

            self._logger.info("=== 工作流執行成功！建立卡片 ID: %d ===", note_id)
            return note_id

        except Exception as e:
            self._logger.error("工作流在執行過程中遭遇失敗: %s", str(e), exc_info=True)
            raise


async def main() -> None:
    """提供可獨立運行的腳本範例入口。"""
    workflow = MainWorkflow()

    toeic_words = [
        "Incorporate",
        "Comprehensive",
        "Implement",
        "Fluctuate",
        "Substantial",
        "Mandatory",
        "Outsource",
        "Discrepancy",
        "Resilient",
        "Revitalize"
    ]

    system_prompt = (
        "你是一位擁有 20 年教學經驗的多益名師。你的任務是將提供的單字解構成專業的學習字卡。\n"
        "必須依循指定的 JSON 格式，並為這些多益單字補充有深度的家族詞性 (Word Family)、"
        "近義詞比較 (Synonyms)、母語者語感微差異，與常考搭配詞分析。"
    )

    print(f"開始批次處理 {len(toeic_words)} 個多益單字...")

    for word in toeic_words:
        try:
            note_id = await workflow.execute(
                deck_name="Default",                  # 目標牌組
                model_name="TOEIC_Coach_Dark",             # 使用暗黑新版模型
                user_prompt=f"請幫我深度分析這個多益單字：{word}",
                model_schema_file="TOEIC_Coach_Dark.json", # 套用最新包含強大陣列格式的 Schema
                system_prompt=system_prompt,
                tags=["TOEIC", "Advanced_Vocab"],
                auto_sync=True                             # 範例演示：強制開啟上傳同步
            )
            print(f"✅ 成功新增筆記！單字: {word}, ID: {note_id}")
            
            # 延遲 2 秒，避免觸發 LLM API 的 Rate Limit 或造成 AnkiConnect 過載
            await asyncio.sleep(2)

        except Exception as e:
            print(f"❌ 單字 {word} 執行失敗: {e}", file=sys.stderr)


if __name__ == "__main__":
    # 配置 Logger 的系統提示輸出
    config.setup_logging()
    # 執行非同步主程式
    asyncio.run(main())
