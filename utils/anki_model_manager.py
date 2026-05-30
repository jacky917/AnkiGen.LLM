"""
Anki 模型管理與組裝 (Anki Model Manager) 模組。

職責：
1. 讀取與管理本地模型定義檔 (.json)。
2. 將 LLM 產生的 JSON 資料封裝成 AnkiConnect 所需的格式。
3. 透過非同步 HTTP (httpx) 提交至 AnkiConnect。
"""

import json
import logging
from pathlib import Path
from typing import Dict

import httpx
from pydantic import BaseModel, ConfigDict, Field

from utils.config_manager import config


class AnkiNoteOptions(BaseModel):
    """定義 AnkiConnect 的 duplicate 檢查行為。"""
    model_config = ConfigDict(populate_by_name=True)
    allowDuplicate: bool = False
    duplicateScope: str = "deck"
    duplicateScopeOptions: dict = Field(default_factory=dict)

class AnkiNotePayload(BaseModel):
    """用於 AnkiConnect `addNote` action 的 params 結構。

    Attributes:
        deckName (str): 目標牌組名稱。
        modelName (str): 目標模型名稱。
        fields (Dict[str, str]): 插入的欄位資料。
        tags (list[str]): 欲加入的標籤。
        options (AnkiNoteOptions | None): 控制重複與否的行為配置。
    """
    model_config = ConfigDict(populate_by_name=True)

    deckName: str
    modelName: str
    fields: Dict[str, str]
    tags: list[str] = Field(default_factory=list)
    options: AnkiNoteOptions | None = None


class AnkiActionContext(BaseModel):
    """封裝發送給 AnkiConnect 的基礎請求結構。

    Attributes:
        action (str): 指定的 AnkiConnect 動作名稱。
        version (int): API 版本（預設 6）。
        params (Dict[str, AnkiNotePayload]): 傳入的參數。
    """
    action: str
    version: int = Field(default=6)
    params: Dict[str, AnkiNotePayload]


class AnkiModelManager:
    """管理 Anki 取出、組裝與提交的類別。

    Attributes:
        _model_dir (Path): 存放 JSON 模型定義檔的資料夾路徑。
        _logger (logging.Logger): 本地日誌記錄器。
    """

    def __init__(self, model_dir: str = "./anki_models") -> None:
        """初始化管理器並設定模型存取路徑。

        Args:
            model_dir (str, optional): 基礎模型資料夾路徑。預設為 "./anki_models"。
        """
        self._model_dir = Path(model_dir)
        self._logger = logging.getLogger(__name__)

        if not self._model_dir.exists():
            self._logger.warning("模型資料夾不存在，將自動建立: %s", self._model_dir)
            self._model_dir.mkdir(parents=True, exist_ok=True)

    def get_model_schema(self, model_file_name: str) -> Dict[str, object]:
        """從本地讀取指定檔案名稱的 Anki 模型定義 JSON Schema。

        支援兩種格式：
        1. 根目錄即為 JSON Schema
        2. 包含 "llm_schema" 鍵值的複合定義檔 (例如包含 modelName 與 inOrderFields)

        Args:
            model_file_name (str): 檔案名稱，例如 "vocabulary_model.json"。

        Returns:
            Dict[str, object]: 包含模型名稱及欄位限制的 JSON Schema 字典。

        Raises:
            FileNotFoundError: 如果找不到對應檔案。
            ValueError: JSON 格式無效。
        """
        file_path: Path = self._model_dir / model_file_name
        if not file_path.is_file():
            self._logger.error("找不到名為 %s 的模型定義檔。", file_path)
            raise FileNotFoundError(f"找不到檔案: {file_path}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data: Dict[str, object] = json.load(f)
                
                # 判斷是否為複合定義檔 (含有 llm_schema 巢狀結構)
                if "llm_schema" in data and isinstance(data["llm_schema"], dict):
                    self._logger.info("檢測到複合模型定義檔，提取 llm_schema: %s", model_file_name)
                    # 這裡加入強制型別標示保證 Mypy 不報錯
                    schema: Dict[str, object] = dict(data["llm_schema"])
                else:
                    self._logger.info("讀取標準模型 Schema: %s", model_file_name)
                    schema = data
                
                return schema
        except json.JSONDecodeError as decode_error:
            self._logger.error("模型定義檔 %s 無效，請檢查 JSON 格式。", file_path)
            raise ValueError(f"無效的 JSON 檔案: {decode_error}")

    def create_add_note_action(
        self,
        deck_name: str,
        model_name: str,
        llm_response: Dict[str, object],
        tags: list[str] | None = None
    ) -> AnkiActionContext:
        """將獲得的 LLM 結構化輸出轉換為 AnkiConnect 的 addNote payload。

        Args:
            deck_name (str): 預定要新增至的牌組。
            model_name (str): 預定新增的模型 (Note Type) 名稱。
            llm_response (Dict[str, object]): 已從 LLM 客戶端取得的結構化 JSON。若包含巢狀 list/dict 則統一序列化為合法 JSON 字串以供 Anki 卡片內 JS 讀取。
            tags (list[str] | None, optional): 附加至卡片的標籤。預設為 None。

        Returns:
            AnkiActionContext: 驗證後的 AnkiConnect 請求 Payload 物件。
        """
        # 利用 json.dumps 確保非字串欄位（如陣列、物件）被轉為合法 JSON 字串
        fields_data: Dict[str, str] = {}
        for k, v in llm_response.items():
            if isinstance(v, (dict, list)):
                fields_data[str(k)] = json.dumps(v, ensure_ascii=False)
            else:
                fields_data[str(k)] = str(v)

        options = AnkiNoteOptions(
            allowDuplicate=False,
            duplicateScope="deck",
            duplicateScopeOptions={"deckName": deck_name, "checkChildren": False, "checkAllModels": False}
        )

        note_payload = AnkiNotePayload(
            deckName=deck_name,
            modelName=model_name,
            fields=fields_data,
            tags=tags or [],
            options=options
        )

        action = AnkiActionContext(
            action="addNote",
            params={"note": note_payload}
        )

        self._logger.debug("成功建立 addNote 請求物件。")
        return action

    async def submit_action(self, action_context: AnkiActionContext) -> int:
        """使用 httpx 非同步提交動作到 AnkiConnect。

        Args:
            action_context (AnkiActionContext): 已被包裹的請求內容。

        Returns:
            int: 成功建立後 Anki 回傳的筆記 ID。

        Raises:
            Exception: 若連線失敗或 AnkiConnect 回傳錯誤（屬 error 欄位有值）則拋出。
        """
        payload: dict[str, object] = action_context.model_dump(by_alias=True)
        self._logger.info("準備送出請求至 AnkiConnect，Action: %s", action_context.action)

        headers = {}
        if config.cf_access_client_id and config.cf_access_client_secret:
            headers.update({
                "CF-Access-Client-Id": config.cf_access_client_id,
                "CF-Access-Client-Secret": config.cf_access_client_secret
            })

        try:
            async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                response = await client.post(config.anki_connect_url, json=payload)
                response.raise_for_status()
                
                response_data: Dict[str, object] = response.json()
                
                # 依循 AnkiConnect v6 標準回覆格式
                error_msg = response_data.get("error")
                if error_msg:
                    if "duplicate" in error_msg.lower():
                        detailed_err = None
                        try:
                            # 嘗試自動尋找衝突的卡片位在哪個牌組
                            note_fields = payload.get("params", {}).get("note", {}).get("fields", {})
                            if note_fields:
                                # 預設對第一個欄位內容進行搜尋 (通常為單字或 Expression)
                                first_field_val = list(note_fields.values())[0]
                                find_payload = {"action": "findNotes", "version": 6, "params": {"query": f'"{first_field_val}"'}}
                                find_res = await client.post(config.anki_connect_url, json=find_payload)
                                note_ids = find_res.json().get("result", [])
                                
                                if note_ids:
                                    info_payload = {"action": "notesInfo", "version": 6, "params": {"notes": note_ids}}
                                    info_res = await client.post(config.anki_connect_url, json=info_payload)
                                    info_data = info_res.json().get("result", [])
                                    
                                    decks = set()
                                    for n_info in info_data:
                                        card_ids = n_info.get("cards", [])
                                        if card_ids:
                                            c_payload = {"action": "cardsInfo", "version": 6, "params": {"cards": [card_ids[0]]}}
                                            c_res = await client.post(config.anki_connect_url, json=c_payload)
                                            c_results = c_res.json().get("result", [])
                                            
                                            # 確認 c_results[0] 存在並且為字典
                                            if c_results and isinstance(c_results[0], dict):
                                                deck_name = c_results[0].get("deckName")
                                                if deck_name:
                                                    decks.add(f"{deck_name}")
                                                else:
                                                    decks.add(f"未知牌組 (nid:{n_info.get('noteId')})")
                                            else:
                                                decks.add(f"找無卡片 (nid:{n_info.get('noteId')})")
                                    
                                    if decks:
                                        deck_list_str = ", ".join(decks)
                                        detailed_err = f"單字/內容 '{first_field_val}' 已經存在！\n👉 本地 Anki 搜尋: nid:{note_ids[0]}\n👉 所屬牌組: [{deck_list_str}]"

                        except Exception as lookup_err:
                            self._logger.warning("嘗試反查重複卡片所在牌組時失敗: %s", str(lookup_err))

                        # 如果成功抓到重複的詳細資訊，則在此拋出 (避開上面的 except 捕捉)
                        if detailed_err:
                            self._logger.error(detailed_err)
                            raise RuntimeError(f"AnkiConnect Duplicate Error: {detailed_err}")

                    self._logger.error("AnkiConnect 回傳內部錯誤: %s", error_msg)
                    raise RuntimeError(f"AnkiConnect Error: {error_msg}")

                result = response_data.get("result")
                if result is None:
                    self._logger.error("AnkiConnect 未回傳有效結果 (None)。")
                    raise RuntimeError("AnkiConnect returned null result without error message.")
                
                # addNote 成功時實質回傳的是該筆記的 ID
                # 轉型為 int 提供更具體的型別標註
                note_id = int(str(result))
                self._logger.info("提交成功，獲取記錄 ID: %d", note_id)
                return note_id

        except httpx.HTTPError as http_error:
            self._logger.error("與 AnkiConnect 之間的通訊發生錯誤: %s", str(http_error))
            raise RuntimeError(f"HTTP request failed: {http_error}")
        except ValueError as value_error:
            self._logger.error("無法將結果解析為整數 ID: %s", str(value_error))
            raise RuntimeError(f"Invalid result format from AnkiConnect: {value_error}")

    async def sync_to_ankiweb(self) -> None:
        """請求 Anki 桌面端執行同步至 AnkiWeb 動作 (本地為主上傳)。

        Raises:
            Exception: 若 API 發生通訊或內部錯誤則拋出。
        """
        self._logger.info("正在請求 Anki 執行同步作業 (Sync)...")
        action_context = {
            "action": "sync",
            "version": 6
        }
        
        headers = {}
        if config.cf_access_client_id and config.cf_access_client_secret:
            headers.update({
                "CF-Access-Client-Id": config.cf_access_client_id,
                "CF-Access-Client-Secret": config.cf_access_client_secret
            })

        try:
            async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
                response = await client.post(config.anki_connect_url, json=action_context)
                response.raise_for_status()
                
                response_data = response.json()
                if response_data.get("error"):
                    self._logger.error("同步失敗: %s", response_data["error"])
                    raise RuntimeError(f"Sync error: {response_data['error']}")
                self._logger.info("AnkiWeb 同步指令已成功發送並完成。")
                
        except httpx.HTTPError as http_error:
            self._logger.error("同步請求時發生通訊錯誤: %s", str(http_error))
            raise RuntimeError(f"Sync HTTP request failed: {http_error}")
            
    async def ensure_deck_exists(self, deck_name: str) -> None:
        """檢查目標牌組是否存在，若否則嘗試從 AnkiWeb 同步一次，再不存在則拋出 RuntimeError。
        
        Args:
            deck_name (str): 預期所在的目標牌組。
            
        Raises:
            RuntimeError: 同步後依舊找不到目標牌組時拋出。
        """
        self._logger.info("檢查目標牌組是否存在: %s", deck_name)
        
        headers = {}
        if config.cf_access_client_id and config.cf_access_client_secret:
            headers.update({
                "CF-Access-Client-Id": config.cf_access_client_id,
                "CF-Access-Client-Secret": config.cf_access_client_secret
            })
            
        async def fetch_decks() -> list[str]:
            payload = {"action": "deckNames", "version": 6}
            async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                resp = await client.post(config.anki_connect_url, json=payload)
                resp.raise_for_status()
                return resp.json().get("result", [])

        decks = await fetch_decks()
        if deck_name in decks:
            return
            
        self._logger.warning("牌組 '%s' 不存在，嘗試執行 Anki 同步 (Sync)...", deck_name)
        await self.sync_to_ankiweb()
        
        decks_after_sync = await fetch_decks()
        if deck_name not in decks_after_sync:
            err_msg = f"已強制同步，但 Anki 內依然未見目標牌組 '{deck_name}'！"
            self._logger.error(err_msg)
            raise RuntimeError(err_msg)
            
        self._logger.info("✅ 同步成功！在 Anki 內順利找到牌組 '%s'，繼續執行後續動作。", deck_name)

    async def can_add_note(
        self,
        deck_name: str,
        model_name: str,
        fields: Dict[str, str]
    ) -> bool:
        """請求 AnkiConnect 檢查給定的筆記是否可以安全新增 (不重複且牌組/模型皆存在)。
        
        這可以在發送昂貴的 LLM 請求之前，先確認 Anki 的存取性以及避免重複生卡。
        
        Args:
            deck_name (str): 目標牌組名稱。
            model_name (str): 目標模型名稱。
            fields (Dict[str, str]): 用來檢測的欄位，建議包含第一欄 (Primary Field) 以供防重機制檢查。
            
        Returns:
            bool: 若可以新增則回傳 True，如果是重複卡片或遇到異常則回傳 False。
            
        Raises:
            RuntimeError: 若與 AnkiConnect 網路斷線或通訊失敗則拋出異常。
        """
        self._logger.info("先期檢查是否可以新增筆記: [%s] at [%s]", model_name, deck_name)
        payload = {
            "action": "canAddNotes",
            "version": 6,
            "params": {
                "notes": [
                    {
                        "deckName": deck_name,
                        "modelName": model_name,
                        "fields": fields
                    }
                ]
            }
        }
        
        headers = {}
        if config.cf_access_client_id and config.cf_access_client_secret:
            headers.update({
                "CF-Access-Client-Id": config.cf_access_client_id,
                "CF-Access-Client-Secret": config.cf_access_client_secret
            })
            
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
                resp = await client.post(config.anki_connect_url, json=payload)
                resp.raise_for_status()
                res_data = resp.json()
                
                if res_data.get("error"):
                    self._logger.error("canAddNotes 回報錯誤: %s", res_data["error"])
                    return False
                    
                results = res_data.get("result", [])
                if results and len(results) > 0:
                    can_add = bool(results[0])
                    if not can_add:
                        self._logger.warning("Anki 拒絕新增此卡片 (可能為重複或模型設定不符)。")
                    return can_add
                return False
                
        except httpx.HTTPError as http_error:
            self._logger.error("canAddNotes 網路中斷或通訊發生錯誤: %s", str(http_error))
            raise RuntimeError(f"AnkiConnect 連線異常: {http_error}")

