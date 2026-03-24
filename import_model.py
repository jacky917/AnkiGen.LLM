"""
Anki 模型匯入腳本 (Model Importer)。

職責：
1. 作為入口，接收指定的模型名稱（例如："TOEIC_Coach"）。
2. 在 `./anki_models/` 底下尋找對應的 `.json`, `_front.html`, `_back.html`, 及 `_style.css`。
3. 封裝上述內容，透過非同步 HTTP 請求 (httpx) 向 AnkiConnect 發送 `createModel` 動作。
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List

import httpx
from pydantic import BaseModel, ConfigDict, Field

from utils.config_manager import config


class AnkiCardTemplate(BaseModel):
    """Anki 模型中的卡片樣板定義。

    Attributes:
        Name (str): 卡片樣板的名稱（例如 "Card 1"）。
        Front (str): 正面 HTML。
        Back (str): 背面 HTML。
    """
    model_config = ConfigDict(populate_by_name=True)

    Name: str
    Front: str
    Back: str


class AnkiModelPayload(BaseModel):
    """用於 AnkiConnect `createModel` action 的 params 結構。

    Attributes:
        modelName (str): 新建立的模型名稱。
        inOrderFields (List[str]): 欄位名稱陣列，需依照順序。
        css (str): 樣式表內容。
        isCloze (bool): 是否為克漏字題型 (預設 False)。
        cardTemplates (List[AnkiCardTemplate]): 包含正背面 HTML 的卡片樣板陣列。
    """
    modelName: str
    inOrderFields: List[str]
    css: str
    isCloze: bool = Field(default=False)
    cardTemplates: List[AnkiCardTemplate]


class AnkiCreateModelContext(BaseModel):
    """封裝發送給 AnkiConnect 的 createModel 請求結構。"""
    action: str = Field(default="createModel")
    version: int = Field(default=6)
    params: AnkiModelPayload


async def import_anki_model(model_name: str, model_dir: str = "./anki_models") -> None:
    """從本地讀取指定模型的四個檔案並匯入至本機的 Anki。

    主要流程：
    1. 讀取 .json 獲取欄位配置 (inOrderFields)。
    2. 讀取 _front.html, _back.html, _style.css 獲取版面配置。
    3. 群組為 AnkiConnect 的 Payload 並發送請求。

    Args:
        model_name (str): 欲匯入的模型名（例如 "TOEIC_Coach"）。
        model_dir (str, optional): 基礎模型資料夾路徑。預設為 "./anki_models"。

    Raises:
        FileNotFoundError: 任一必要檔案丟失時。
        ValueError: JSON 檔案格式錯誤時。
        RuntimeError: AnkiConnect 回傳內部錯誤時。
    """
    logger = logging.getLogger(__name__)
    base_path = Path(model_dir)

    json_path = base_path / f"{model_name}.json"
    front_path = base_path / f"{model_name}_front.html"
    back_path = base_path / f"{model_name}_back.html"
    css_path = base_path / f"{model_name}_style.css"

    # 1. 檢查檔案完整性
    for path in [json_path, front_path, back_path, css_path]:
        if not path.is_file():
            logger.error("缺少必要匯入文件：%s", path)
            raise FileNotFoundError(f"找不到檔案: {path}")

    logger.info("檢測到 4 份必要文件，正在讀取模型: %s", model_name)

    # 2. 準備讀取變數
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data: Dict[str, object] = json.load(f)

            if "inOrderFields" not in data or not isinstance(data["inOrderFields"], list):
                raise ValueError("JSON 定義檔內缺少 'inOrderFields' 陣列定義。")
            in_order_fields: List[str] = [str(item) for item in data["inOrderFields"]]

            # 兼容：如果 JSON 頂層沒寫 modelName，預設使用傳入檔名
            actual_model_name: str = str(data.get("modelName", model_name))

    except json.JSONDecodeError as decode_error:
        logger.error("解析 %s 時發生 JSON 錯誤。", json_path)
        raise ValueError(f"無效的 JSON 檔案: {decode_error}")

    with open(front_path, "r", encoding="utf-8") as f:
        front_html = f.read()
    with open(back_path, "r", encoding="utf-8") as f:
        back_html = f.read()
    with open(css_path, "r", encoding="utf-8") as f:
        css_style = f.read()

    # 3. 封裝 Pydantic 驗證結構
    template = AnkiCardTemplate(
        Name="Card 1",
        Front=front_html,
        Back=back_html
    )

    payload = AnkiModelPayload(
        modelName=actual_model_name,
        inOrderFields=in_order_fields,
        css=css_style,
        isCloze=False,
        cardTemplates=[template]
    )

    action_context = AnkiCreateModelContext(params=payload)

    # 4. 透過 httpx 發送至 AnkiConnect
    logger.info("檔案檢驗通過，準備向 AnkiConnect 提交建置請求...")
    post_data: dict[str, object] = action_context.model_dump(by_alias=True)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(config.anki_connect_url, json=post_data)
            response.raise_for_status()

            response_data: Dict[str, object] = response.json()
            error_msg = response_data.get("error")

            if error_msg:
                logger.error("AnkiConnect 回傳內部錯誤: %s", error_msg)
                raise RuntimeError(f"AnkiConnect Error: {error_msg}")

            # 成功時回傳 result 資料
            result = response_data.get("result")
            logger.info("=== 模型 [%s] 成功匯入至 Anki！ ===", actual_model_name)
            logger.debug("AnkiConnect 訊息: %s", result)

    except httpx.HTTPError as http_error:
        logger.error("與 AnkiConnect 之間的通訊發生錯誤: %s", str(http_error))
        raise RuntimeError(f"HTTP request failed: {http_error}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="導入本地 Anki 模型定義至 Anki 應用程式。")
    parser.add_argument(
        "-model_name",
        type=str,
        help="目標模型的名稱（請勿加上副檔名，對應 anki_models 目錄下的檔案前綴，例如 TOEIC_Coach）"
    )
    args = parser.parse_args()

    # 配置 Logging
    config.setup_logging()
    
    # 執行非同步主程式
    try:
        asyncio.run(import_anki_model(args.model_name))
    except Exception as e:
        print(f"匯入失敗: {e}", file=sys.stderr)
        sys.exit(1)
