"""
AnkiConnect CRUD 類別模組

本模組提供一個完整的 AnkiConnect API v6 封裝類別，支援牌組（Deck）、筆記（Note）、
卡片（Card）、媒體（Media）、模型（Model）以及雜項（Miscellaneous）操作。

所有敏感資訊（如伺服器地址、端口、API 金鑰）皆從 .env 檔案讀取，
不會在程式碼中硬編碼任何機密資料。

Dependencies:
    - requests: HTTP 請求庫
    - python-dotenv: .env 檔案載入

Usage:
    >>> from utils.anki_connect import AnkiConnect
    >>> ac = AnkiConnect()
    >>> print(ac.get_version())
    6
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests
from dotenv import load_dotenv

# ============================================================================
# 載入 .env 環境變數
# 使用 Path 定位專案根目錄下的 .env 檔案，確保無論從哪裡執行都能正確載入
# ============================================================================
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)

# ============================================================================
# 設定日誌記錄器
# ============================================================================
logger = logging.getLogger(__name__)


class AnkiConnectError(Exception):
    """AnkiConnect API 錯誤異常類別

    當 AnkiConnect API 回傳錯誤時拋出此異常。

    Attributes:
        message: 錯誤訊息字串，來自 AnkiConnect 的 error 欄位。
    """

    def __init__(self, message: str) -> None:
        """初始化 AnkiConnectError。

        Args:
            message: AnkiConnect API 回傳的錯誤訊息。
        """
        super().__init__(message)
        self.message = message


class AnkiConnect:
    """AnkiConnect API v6 完整 CRUD 封裝類別

    提供對 AnkiConnect 所有主要功能的 Python 封裝，包括：
    - 牌組（Deck）管理：建立、刪除、查詢牌組
    - 筆記（Note）管理：新增、更新、刪除、搜尋筆記
    - 卡片（Card）管理：搜尋、暫停/取消暫停、設定簡易度
    - 媒體（Media）管理：儲存、讀取、刪除媒體檔案
    - 模型（Model）查詢：取得模型名稱及欄位
    - 雜項操作：版本查詢、同步、權限請求

    所有連線資訊從環境變數讀取：
    - ANKI_CONNECT_HOST: 伺服器地址（預設 127.0.0.1）
    - ANKI_CONNECT_PORT: 伺服器端口（預設 8765）
    - ANKI_CONNECT_API_KEY: API 金鑰（可選）

    Example:
        >>> ac = AnkiConnect()
        >>> decks = ac.get_deck_names()
        >>> print(decks)
        ['Default', 'Japanese::JLPT N3']
    """

    # AnkiConnect API 版本，固定使用 v6 以獲得完整錯誤處理支援
    API_VERSION = 6

    # HTTP 請求預設超時秒數
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """初始化 AnkiConnect 客戶端。

        優先使用參數傳入的值，若未提供則從環境變數讀取，最後使用預設值。

        Args:
            host: AnkiConnect 伺服器地址。若為 None，從 ANKI_CONNECT_HOST 環境變數讀取，
                  預設為 '127.0.0.1'。
            port: AnkiConnect 伺服器端口。若為 None，從 ANKI_CONNECT_PORT 環境變數讀取，
                  預設為 8765。
            api_key: AnkiConnect API 金鑰。若為 None，從 ANKI_CONNECT_API_KEY 環境變數讀取。
                     若伺服器未啟用認證，可留空。
            timeout: HTTP 請求超時秒數，預設 30 秒。

        Example:
            >>> # 使用環境變數中的預設配置
            >>> ac = AnkiConnect()
            >>> # 自訂連線參數
            >>> ac = AnkiConnect(host='192.168.1.100', port=8765, api_key='my_secret')
        """
        # 首先嘗試從環境變數讀取完整的 URL (與 config_manager.py 同步)
        env_url = os.getenv("ANKI_CONNECT_URL")
        if env_url:
            self._url = env_url.rstrip("/")
        else:
            # 推回使用原本的 host / port 邏輯
            self._host = host or os.getenv("ANKI_CONNECT_HOST", "127.0.0.1")
            self._port = port or int(os.getenv("ANKI_CONNECT_PORT", "8765"))
            self._url = f"http://{self._host}:{self._port}"

        # 從環境變數讀取 API 金鑰，空字串視為未設定
        self._api_key = api_key or os.getenv("ANKI_CONNECT_API_KEY", "") or None

        # 設定 HTTP 請求超時時間
        self._timeout = timeout

        # 建立 requests.Session 以提升連線效能（連線池復用）
        self._session = requests.Session()
        
        # 若有設定 Cloudflare Access 憑證，則統一寫入 Session Header 供遠端 API 穿透保護使用
        cf_client_id = os.getenv("CF_ACCESS_CLIENT_ID")
        cf_client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET")
        if cf_client_id and cf_client_secret:
            self._session.headers.update({
                "CF-Access-Client-Id": cf_client_id,
                "CF-Access-Client-Secret": cf_client_secret
            })

        logger.info("AnkiConnect 客戶端已初始化，目標地址: %s", self._url)

    # ========================================================================
    # 核心方法（Core）
    # ========================================================================

    def _invoke(self, action: str, **params: Any) -> Any:
        """向 AnkiConnect 發送 API 請求的底層方法。

        組裝 JSON 請求體，發送 HTTP POST 請求至 AnkiConnect 伺服器，
        並解析回應結果。此方法是所有公開 API 方法的基礎。

        請求格式：
            {
                "action": "<action_name>",
                "version": 6,
                "params": { ... },
                "key": "<api_key>"  // 僅在設定 API 金鑰時包含
            }

        回應格式：
            {
                "result": <any>,    // 成功時的回傳值
                "error": <string|null>  // 錯誤訊息或 null
            }

        Args:
            action: AnkiConnect API 動作名稱，例如 'deckNames'、'addNote' 等。
            **params: 傳遞給 API 動作的關鍵字參數，將被轉換為 JSON 的 params 欄位。

        Returns:
            API 回應中的 result 欄位值，型別取決於具體的 API 動作。

        Raises:
            AnkiConnectError: 當 AnkiConnect 回傳 error 欄位不為 null 時。
            requests.exceptions.ConnectionError: 當無法連接到 AnkiConnect 伺服器時。
            requests.exceptions.Timeout: 當請求超時時。

        Example:
            >>> ac = AnkiConnect()
            >>> result = ac._invoke('deckNames')
            >>> print(result)
            ['Default']
        """
        # 步驟 1：組裝請求 payload，包含 action 名稱和 API 版本號
        payload: Dict[str, Any] = {
            "action": action,
            "version": self.API_VERSION,
        }

        # 步驟 2：若有傳入參數，加入 params 欄位
        if params:
            payload["params"] = params

        # 步驟 3：若有設定 API 金鑰，加入 key 欄位以通過伺服器認證
        if self._api_key:
            payload["key"] = self._api_key

        # 記錄 API 呼叫日誌（不記錄 key 以避免洩露敏感資訊）
        logger.debug(
            "發送 AnkiConnect 請求 -> action: %s, params: %s",
            action,
            params if params else "無",
        )

        try:
            # 步驟 4：發送 HTTP POST 請求至 AnkiConnect 伺服器
            # - json 參數自動將 dict 序列化為 JSON 並設定 Content-Type header
            # - timeout 防止請求無限期等待
            response = self._session.post(
                self._url,
                json=payload,
                timeout=self._timeout,
            )

            # 步驟 5：檢查 HTTP 狀態碼，非 2xx 會拋出 HTTPError
            response.raise_for_status()

        except requests.exceptions.ConnectionError:
            # 無法連接到 AnkiConnect 時，提供友善的錯誤提示
            logger.error("無法連接到 AnkiConnect 伺服器: %s", self._url)
            raise AnkiConnectError(
                f"無法連接到 AnkiConnect 伺服器 ({self._url})。"
                f"請確認 Anki 已啟動且 AnkiConnect 插件已安裝。"
            )
        except requests.exceptions.Timeout:
            # 請求超時的錯誤處理
            logger.error("AnkiConnect 請求超時: action=%s", action)
            raise AnkiConnectError(
                f"AnkiConnect 請求超時（{self._timeout}秒），action: {action}"
            )

        # 步驟 6：解析 JSON 回應
        result = response.json()

        # 步驟 7：驗證回應格式是否包含必要的 result 和 error 欄位
        if "error" not in result:
            raise AnkiConnectError("回應缺少必要的 error 欄位")
        if "result" not in result:
            raise AnkiConnectError("回應缺少必要的 result 欄位")

        # 步驟 8：若 error 欄位不為 null，拋出 AnkiConnectError
        if result["error"] is not None:
            logger.error("AnkiConnect API 錯誤: %s (action: %s)", result["error"], action)
            raise AnkiConnectError(result["error"])

        logger.debug("AnkiConnect 請求成功 -> action: %s", action)

        # 步驟 9：回傳 result 欄位的值
        return result["result"]

    # ========================================================================
    # 牌組操作（Deck Actions）
    # ========================================================================

    def get_deck_names(self) -> List[str]:
        """取得所有牌組名稱。

        Returns:
            包含所有牌組名稱的字串列表。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_deck_names()
            ['Default', 'Japanese::JLPT N3']
        """
        return self._invoke("deckNames")

    def get_deck_names_and_ids(self) -> Dict[str, int]:
        """取得所有牌組名稱及其對應的 ID。

        Returns:
            字典，鍵為牌組名稱（str），值為牌組 ID（int）。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_deck_names_and_ids()
            {'Default': 1, 'Japanese::JLPT N3': 1519323742721}
        """
        return self._invoke("deckNamesAndIds")

    def create_deck(self, deck: str) -> int:
        """建立新的牌組。

        若已存在同名牌組，不會覆蓋。支援使用 '::' 分隔符建立巢狀牌組。

        Args:
            deck: 牌組名稱。使用 '::' 建立子牌組，例如 'Japanese::Tokyo'。

        Returns:
            新建立牌組的 ID。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.create_deck('English::Vocabulary')
            1519323742721
        """
        return self._invoke("createDeck", deck=deck)

    def delete_decks(self, decks: List[str], cards_too: bool = True) -> None:
        """刪除指定的牌組。

        Args:
            decks: 要刪除的牌組名稱列表。
            cards_too: 是否同時刪除牌組中的卡片。AnkiConnect 要求此值必須為 True。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.delete_decks(['Japanese::JLPT N5', 'Easy Spanish'])
        """
        self._invoke("deleteDecks", decks=decks, cardsToo=cards_too)

    def get_deck_config(self, deck: str) -> Dict[str, Any]:
        """取得指定牌組的設定群組物件。

        Args:
            deck: 牌組名稱。

        Returns:
            包含牌組設定的字典，包括新卡片設定（new）、複習設定（rev）、
            遺忘設定（lapse）等。

        Raises:
            AnkiConnectError: API 請求失敗時（例如牌組不存在）。

        Example:
            >>> config = ac.get_deck_config('Default')
            >>> print(config['new']['perDay'])
            20
        """
        return self._invoke("getDeckConfig", deck=deck)

    def change_deck(self, cards: List[int], deck: str) -> None:
        """將指定卡片移動到另一個牌組。

        若目標牌組不存在，會自動建立。

        Args:
            cards: 要移動的卡片 ID 列表。
            deck: 目標牌組名稱。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.change_deck([1502098034045, 1502098034048], 'Japanese::JLPT N3')
        """
        self._invoke("changeDeck", cards=cards, deck=deck)

    def get_decks(self, cards: List[int]) -> Dict[str, List[int]]:
        """根據卡片 ID 取得其所屬的牌組。

        Args:
            cards: 卡片 ID 列表。

        Returns:
            字典，鍵為牌組名稱，值為屬於該牌組的卡片 ID 列表。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_decks([1502298036657, 1502032366472])
            {'Default': [1502032366472], 'Japanese::JLPT N3': [1502298036657]}
        """
        return self._invoke("getDecks", cards=cards)

    def duplicate_deck(self, source_deck: str, destination_deck: str) -> None:
        """複製牌組。

        將源牌組（source_deck）的所有筆記完整複製到目的牌組（destination_deck），
        包含筆記類型（model）、欄位內容（fields）和標籤（tags）。
        若源牌組不存在，或是目的牌組已存在，將印出錯誤訊息並終止程式。

        Args:
            source_deck: 來源牌組名稱。
            destination_deck: 目的牌組名稱。

        Raises:
            SystemExit: 源牌組不存在或目的牌組已存在時退出程式。
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.duplicate_deck('Template Deck', 'New Deck')
        """
        import sys
        decks = self.get_deck_names()
        
        if source_deck not in decks:
            logger.error(f"源牌組 '{source_deck}' 不存在。")
            print(f"錯誤：源牌組 '{source_deck}' 不存在。")
            sys.exit(1)
            
        if destination_deck in decks:
            logger.error(f"目的牌組 '{destination_deck}' 已存在。")
            print(f"錯誤：目的牌組 '{destination_deck}' 已存在。")
            sys.exit(1)

        # 取得源牌組內所有筆記 ID
        note_ids = self.find_notes(f'"deck:{source_deck}"')
        
        # 建立目的牌組
        self.create_deck(destination_deck)
        
        if not note_ids:
            logger.warning(f"源牌組 '{source_deck}' 中沒有任何筆記。僅建立空的目的牌組 '{destination_deck}'。")
            return
            
        # 取得源牌組所有筆記的詳細資訊
        notes_info = self.get_notes_info(notes=note_ids)
        
        # 準備要新增的筆記列表
        new_notes = []
        for info in notes_info:
            fields = {k: v['value'] for k, v in info['fields'].items()}
            
            new_notes.append({
                "deckName": destination_deck,
                "modelName": info['modelName'],
                "fields": fields,
                "tags": info['tags'],
                "options": {
                    "allowDuplicate": True
                }
            })
            
        # 批次新增筆記至目的牌組
        if new_notes:
            self.add_notes(new_notes)
            logger.info(f"成功複製 {len(new_notes)} 則筆記從 '{source_deck}' 到 '{destination_deck}'。")

    # ========================================================================
    # 筆記操作（Note Actions）
    # ========================================================================

    def add_note(
        self,
        deck_name: str,
        model_name: str,
        fields: Dict[str, str],
        tags: Optional[List[str]] = None,
        allow_duplicate: bool = False,
        duplicate_scope: Optional[str] = None,
        duplicate_scope_options: Optional[Dict[str, Any]] = None,
        audio: Optional[List[Dict[str, Any]]] = None,
        video: Optional[List[Dict[str, Any]]] = None,
        picture: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[int]:
        """新增一則筆記到指定牌組。

        使用指定的模型和欄位值建立新筆記，可選擇附加標籤和媒體檔案。

        Args:
            deck_name: 目標牌組名稱。
            model_name: 筆記類型（模型）名稱，例如 'Basic'、'Cloze'。
            fields: 筆記欄位字典，鍵為欄位名稱，值為欄位內容。
                    例如 {'Front': '你好', 'Back': 'Hello'}。
            tags: 標籤列表，可選。
            allow_duplicate: 是否允許重複筆記，預設 False。
            duplicate_scope: 重複檢查範圍。'deck' 僅檢查目標牌組，
                             其他值或 None 檢查整個集合。
            duplicate_scope_options: 重複檢查進階選項字典，可包含：
                - deckName (str): 指定用於重複檢查的牌組
                - checkChildren (bool): 是否檢查子牌組，預設 False
                - checkAllModels (bool): 是否跨模型檢查，預設 False
            audio: 音訊附件列表，每個元素為包含 url/data/path 和 filename 的字典。
            video: 影片附件列表，格式同 audio。
            picture: 圖片附件列表，格式同 audio。

        Returns:
            新建筆記的 ID（int），若建立失敗則回傳 None。

        Raises:
            AnkiConnectError: API 請求失敗時（例如模型不存在、欄位不匹配）。

        Example:
            >>> note_id = ac.add_note(
            ...     deck_name='Default',
            ...     model_name='Basic',
            ...     fields={'Front': 'apple', 'Back': '蘋果'},
            ...     tags=['english', 'fruit'],
            ... )
            >>> print(note_id)
            1496198395707
        """
        # 組裝筆記物件，包含牌組、模型和欄位
        note: Dict[str, Any] = {
            "deckName": deck_name,
            "modelName": model_name,
            "fields": fields,
        }

        # 組裝選項物件，控制重複檢查行為
        options: Dict[str, Any] = {"allowDuplicate": allow_duplicate}
        if duplicate_scope is not None:
            options["duplicateScope"] = duplicate_scope
        if duplicate_scope_options is not None:
            options["duplicateScopeOptions"] = duplicate_scope_options
        note["options"] = options

        # 若有標籤，加入筆記物件
        if tags is not None:
            note["tags"] = tags

        # 若有媒體附件，分別加入筆記物件
        if audio is not None:
            note["audio"] = audio
        if video is not None:
            note["video"] = video
        if picture is not None:
            note["picture"] = picture

        return self._invoke("addNote", note=note)

    def add_notes(self, notes: List[Dict[str, Any]]) -> List[Optional[int]]:
        """批次新增多則筆記。

        每個筆記物件的格式與 addNote 相同。若任一筆記建立失敗，
        所有錯誤會被收集後一起回傳。

        Args:
            notes: 筆記物件列表，每個元素為包含 deckName、modelName、fields 等的字典。
                   格式範例：
                   [
                       {
                           'deckName': 'Default',
                           'modelName': 'Basic',
                           'fields': {'Front': 'Q1', 'Back': 'A1'},
                           'tags': ['tag1'],
                       },
                       ...
                   ]

        Returns:
            筆記 ID 列表，失敗的筆記對應位置為 None。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ids = ac.add_notes([
            ...     {'deckName': 'Default', 'modelName': 'Basic',
            ...      'fields': {'Front': 'Q1', 'Back': 'A1'}},
            ...     {'deckName': 'Default', 'modelName': 'Basic',
            ...      'fields': {'Front': 'Q2', 'Back': 'A2'}},
            ... ])
        """
        return self._invoke("addNotes", notes=notes)

    def update_note(
        self,
        note_id: int,
        fields: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        audio: Optional[List[Dict[str, Any]]] = None,
        video: Optional[List[Dict[str, Any]]] = None,
        picture: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """更新現有筆記的欄位和/或標籤。

        結合 updateNoteFields 和 updateNoteTags 的功能。
        fields 和 tags 可擇一省略而不影響另一個。

        注意：更新時請勿在 Anki 瀏覽器中檢視該筆記，否則欄位可能不會更新。

        Args:
            note_id: 要更新的筆記 ID。
            fields: 要更新的欄位字典，鍵為欄位名稱，值為新內容。可選。
            tags: 新的標籤列表，將取代舊標籤。可選。
            audio: 音訊附件列表（需同時提供 fields）。可選。
            video: 影片附件列表（需同時提供 fields）。可選。
            picture: 圖片附件列表（需同時提供 fields）。可選。

        Raises:
            AnkiConnectError: 若 fields 和 tags 皆未提供，或筆記不存在。

        Example:
            >>> ac.update_note(
            ...     note_id=1514547547030,
            ...     fields={'Front': 'updated front', 'Back': 'updated back'},
            ...     tags=['updated', 'tags'],
            ... )
        """
        # 組裝筆記更新物件，必須包含 id
        note: Dict[str, Any] = {"id": note_id}

        # 加入要更新的欄位
        if fields is not None:
            note["fields"] = fields

        # 加入新標籤列表
        if tags is not None:
            note["tags"] = tags

        # 加入媒體附件（必須與 fields 一起提供才有效）
        if audio is not None:
            note["audio"] = audio
        if video is not None:
            note["video"] = video
        if picture is not None:
            note["picture"] = picture

        self._invoke("updateNote", note=note)

    def update_note_fields(
        self,
        note_id: int,
        fields: Dict[str, str],
        audio: Optional[List[Dict[str, Any]]] = None,
        video: Optional[List[Dict[str, Any]]] = None,
        picture: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """更新現有筆記的欄位內容。

        僅更新欄位，不影響標籤。可同時附加媒體檔案。

        注意：更新時請勿在 Anki 瀏覽器中檢視該筆記，否則欄位可能不會更新。

        Args:
            note_id: 要更新的筆記 ID。
            fields: 要更新的欄位字典，鍵為欄位名稱，值為新內容。
            audio: 音訊附件列表。可選。
            video: 影片附件列表。可選。
            picture: 圖片附件列表。可選。

        Raises:
            AnkiConnectError: 筆記不存在或欄位名稱不匹配時。

        Example:
            >>> ac.update_note_fields(
            ...     note_id=1514547547030,
            ...     fields={'Front': 'new question', 'Back': 'new answer'},
            ... )
        """
        # 組裝筆記物件
        note: Dict[str, Any] = {"id": note_id, "fields": fields}

        # 加入可選的媒體附件
        if audio is not None:
            note["audio"] = audio
        if video is not None:
            note["video"] = video
        if picture is not None:
            note["picture"] = picture

        self._invoke("updateNoteFields", note=note)

    def delete_notes(self, notes: List[int]) -> None:
        """刪除指定的筆記。

        若筆記有多張關聯卡片，所有關聯卡片都會被一起刪除。

        Args:
            notes: 要刪除的筆記 ID 列表。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.delete_notes([1502298033753, 1502298033754])
        """
        self._invoke("deleteNotes", notes=notes)

    def find_notes(self, query: str) -> List[int]:
        """使用 Anki 搜尋語法查找筆記。

        搜尋語法文件：https://docs.ankiweb.net/searching.html

        Args:
            query: Anki 搜尋查詢語句。
                   常用範例：
                   - 'deck:Default' — 指定牌組中的所有筆記
                   - 'tag:english' — 含有指定標籤的筆記
                   - '"front content"' — 包含指定文字的筆記
                   - 'added:7' — 最近 7 天新增的筆記

        Returns:
            符合條件的筆記 ID 列表。

        Raises:
            AnkiConnectError: 查詢語法錯誤或 API 請求失敗時。

        Example:
            >>> note_ids = ac.find_notes('deck:Default tag:english')
            >>> print(note_ids)
            [1483959289817, 1483959291695]
        """
        return self._invoke("findNotes", query=query)

    def get_notes_info(
        self,
        notes: Optional[List[int]] = None,
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """取得筆記的詳細資訊。

        可透過筆記 ID 列表或搜尋查詢語句取得資訊。兩者擇一即可。

        Args:
            notes: 筆記 ID 列表。與 query 二選一。
            query: Anki 搜尋查詢語句。與 notes 二選一。

        Returns:
            筆記資訊字典列表，每個字典包含：
            - noteId (int): 筆記 ID
            - modelName (str): 模型名稱
            - tags (list): 標籤列表
            - fields (dict): 欄位內容
            - mod (int): 最後修改時間戳
            - cards (list): 關聯卡片 ID 列表

        Raises:
            AnkiConnectError: API 請求失敗時。
            ValueError: notes 和 query 皆未提供時。

        Example:
            >>> infos = ac.get_notes_info(notes=[1502298033753])
            >>> print(infos[0]['fields']['Front']['value'])
            'front content'
        """
        if notes is not None:
            return self._invoke("notesInfo", notes=notes)
        elif query is not None:
            return self._invoke("notesInfo", query=query)
        else:
            raise ValueError("必須提供 notes 或 query 參數其中之一")

    def add_tags(self, notes: List[int], tags: str) -> None:
        """為指定筆記新增標籤。

        Args:
            notes: 筆記 ID 列表。
            tags: 要新增的標籤，多個標籤以空格分隔。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.add_tags([1483959289817, 1483959291695], 'english vocabulary')
        """
        self._invoke("addTags", notes=notes, tags=tags)

    def remove_tags(self, notes: List[int], tags: str) -> None:
        """移除指定筆記的標籤。

        Args:
            notes: 筆記 ID 列表。
            tags: 要移除的標籤，多個標籤以空格分隔。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.remove_tags([1483959289817], 'old-tag')
        """
        self._invoke("removeTags", notes=notes, tags=tags)

    def get_tags(self) -> List[str]:
        """取得當前使用者的所有標籤。

        Returns:
            標籤名稱字串列表。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_tags()
            ['english', 'japanese', 'vocabulary']
        """
        return self._invoke("getTags")

    def replace_tags(
        self,
        notes: List[int],
        tag_to_replace: str,
        replace_with_tag: str,
    ) -> None:
        """替換指定筆記中的標籤。

        Args:
            notes: 筆記 ID 列表。
            tag_to_replace: 要被替換的標籤名稱。
            replace_with_tag: 替換後的新標籤名稱。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.replace_tags([1483959289817], 'old-tag', 'new-tag')
        """
        self._invoke(
            "replaceTags",
            notes=notes,
            tag_to_replace=tag_to_replace,
            replace_with_tag=replace_with_tag,
        )

    def replace_tags_in_all_notes(
        self,
        tag_to_replace: str,
        replace_with_tag: str,
    ) -> None:
        """在所有筆記中替換指定標籤。

        Args:
            tag_to_replace: 要被替換的標籤名稱。
            replace_with_tag: 替換後的新標籤名稱。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.replace_tags_in_all_notes('european-languages', 'french')
        """
        self._invoke(
            "replaceTagsInAllNotes",
            tag_to_replace=tag_to_replace,
            replace_with_tag=replace_with_tag,
        )

    def clear_unused_tags(self) -> None:
        """清除所有未使用的標籤。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.clear_unused_tags()
        """
        self._invoke("clearUnusedTags")

    def can_add_notes(self, notes: List[Dict[str, Any]]) -> List[bool]:
        """檢查筆記是否可以被新增（不實際建立）。

        用於在批次新增前驗證筆記參數是否合法。

        Args:
            notes: 筆記物件列表，格式同 add_note 的參數。

        Returns:
            布林值列表，對應每個筆記是否可以被新增。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> can_add = ac.can_add_notes([{
            ...     'deckName': 'Default',
            ...     'modelName': 'Basic',
            ...     'fields': {'Front': 'test', 'Back': 'test'},
            ... }])
            >>> print(can_add)
            [True]
        """
        return self._invoke("canAddNotes", notes=notes)

    # ========================================================================
    # 卡片操作（Card Actions）
    # ========================================================================

    def find_cards(self, query: str) -> List[int]:
        """使用 Anki 搜尋語法查找卡片。

        Args:
            query: Anki 搜尋查詢語句。

        Returns:
            符合條件的卡片 ID 列表。

        Raises:
            AnkiConnectError: 查詢語法錯誤或 API 請求失敗時。

        Example:
            >>> card_ids = ac.find_cards('deck:Default is:due')
            >>> print(card_ids)
            [1502298033753, 1502298036657]
        """
        return self._invoke("findCards", query=query)

    def get_cards_info(self, cards: List[int]) -> List[Dict[str, Any]]:
        """取得卡片的詳細資訊。

        Args:
            cards: 卡片 ID 列表。

        Returns:
            卡片資訊字典列表，每個字典包含 cardId、fields、deckName、
            modelName、interval、note 等資訊。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> infos = ac.get_cards_info([1502298033753])
            >>> print(infos[0]['deckName'])
            'Default'
        """
        return self._invoke("cardsInfo", cards=cards)

    def suspend_cards(self, cards: List[int]) -> bool:
        """暫停指定的卡片。

        暫停後的卡片不會出現在複習排程中。

        Args:
            cards: 要暫停的卡片 ID 列表。

        Returns:
            True 若至少有一張卡片被成功暫停（之前非暫停狀態），否則 False。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.suspend_cards([1483959291685, 1483959293217])
            True
        """
        return self._invoke("suspend", cards=cards)

    def unsuspend_cards(self, cards: List[int]) -> bool:
        """取消暫停指定的卡片。

        Args:
            cards: 要取消暫停的卡片 ID 列表。

        Returns:
            True 若至少有一張卡片被成功取消暫停（之前為暫停狀態），否則 False。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.unsuspend_cards([1483959291685])
            True
        """
        return self._invoke("unsuspend", cards=cards)

    def get_ease_factors(self, cards: List[int]) -> List[int]:
        """取得指定卡片的簡易度因子。

        Args:
            cards: 卡片 ID 列表。

        Returns:
            簡易度因子列表（與輸入順序對應），常見值如 2500（預設）。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_ease_factors([1483959291685, 1483959293217])
            [4100, 3900]
        """
        return self._invoke("getEaseFactors", cards=cards)

    def set_ease_factors(
        self, cards: List[int], ease_factors: List[int]
    ) -> List[bool]:
        """設定指定卡片的簡易度因子。

        Args:
            cards: 卡片 ID 列表。
            ease_factors: 對應的新簡易度因子列表，長度必須與 cards 相同。

        Returns:
            布林值列表，表示每張卡片是否設定成功（卡片存在則為 True）。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.set_ease_factors([1483959291685], [2500])
            [True]
        """
        return self._invoke("setEaseFactors", cards=cards, easeFactors=ease_factors)

    def are_suspended(self, cards: List[int]) -> List[Optional[bool]]:
        """批次檢查卡片是否處於暫停狀態。

        Args:
            cards: 卡片 ID 列表。

        Returns:
            布林值列表（與輸入順序對應）：
            - True: 卡片已暫停
            - False: 卡片未暫停
            - None: 卡片不存在

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.are_suspended([1483959291685, 9999999999999])
            [False, None]
        """
        return self._invoke("areSuspended", cards=cards)

    # ========================================================================
    # 媒體操作（Media Actions）
    # ========================================================================

    def store_media_file(
        self,
        filename: str,
        data: Optional[str] = None,
        path: Optional[str] = None,
        url: Optional[str] = None,
        delete_existing: bool = True,
    ) -> str:
        """儲存媒體檔案到 Anki 的媒體資料夾。

        提供三種方式指定檔案內容（優先順序：data > path > url）：
        1. data: Base64 編碼的檔案內容
        2. path: 本地檔案的絕對路徑
        3. url: 遠端檔案的下載 URL

        若檔案名稱以底線 '_' 開頭，Anki 不會在同步時刪除未使用的檔案。

        Args:
            filename: 檔案名稱（含副檔名）。
            data: Base64 編碼的檔案內容。可選。
            path: 本地檔案絕對路徑。可選。
            url: 遠端檔案 URL。可選。
            delete_existing: 是否刪除同名的既有檔案，預設 True。
                             設為 False 時 Anki 會自動為新檔案產生不衝突的名稱。

        Returns:
            實際儲存的檔案名稱（可能與輸入不同，若 delete_existing=False 且有衝突時）。

        Raises:
            AnkiConnectError: API 請求失敗時。
            ValueError: data、path、url 皆未提供時。

        Example:
            >>> ac.store_media_file(
            ...     filename='image.jpg',
            ...     url='https://example.com/image.jpg',
            ... )
            'image.jpg'
        """
        # 驗證至少提供一種檔案來源
        if data is None and path is None and url is None:
            raise ValueError("必須提供 data、path 或 url 參數其中之一")

        # 組裝參數字典
        params: Dict[str, Any] = {
            "filename": filename,
            "deleteExisting": delete_existing,
        }

        # 按優先順序加入檔案來源
        if data is not None:
            params["data"] = data
        elif path is not None:
            params["path"] = path
        elif url is not None:
            params["url"] = url

        return self._invoke("storeMediaFile", **params)

    def retrieve_media_file(self, filename: str) -> Union[str, bool]:
        """讀取 Anki 媒體資料夾中的檔案內容。

        Args:
            filename: 檔案名稱。

        Returns:
            Base64 編碼的檔案內容字串，若檔案不存在則回傳 False。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> content = ac.retrieve_media_file('_hello.txt')
            >>> import base64
            >>> print(base64.b64decode(content).decode())
            'Hello, world!'
        """
        return self._invoke("retrieveMediaFile", filename=filename)

    def get_media_files_names(self, pattern: str = "*") -> List[str]:
        """搜尋符合模式的媒體檔案名稱。

        Args:
            pattern: glob 模式字串，預設 '*' 回傳所有檔案。
                     範例：'*.mp3'、'_config*'。

        Returns:
            符合模式的檔案名稱列表。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_media_files_names('*.jpg')
            ['image1.jpg', 'image2.jpg']
        """
        return self._invoke("getMediaFilesNames", pattern=pattern)

    def delete_media_file(self, filename: str) -> None:
        """刪除 Anki 媒體資料夾中的指定檔案。

        Args:
            filename: 要刪除的檔案名稱。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.delete_media_file('old_image.jpg')
        """
        self._invoke("deleteMediaFile", filename=filename)

    def get_media_dir_path(self) -> str:
        """取得當前設定檔的媒體資料夾完整路徑。

        Returns:
            媒體資料夾的絕對路徑字串。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_media_dir_path()
            '/home/user/.local/share/Anki2/Main/collection.media'
        """
        return self._invoke("getMediaDirPath")

    # ========================================================================
    # 模型操作（Model Actions）
    # ========================================================================

    def get_model_names(self) -> List[str]:
        """取得所有模型（筆記類型）名稱。

        Returns:
            模型名稱字串列表。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_model_names()
            ['Basic', 'Basic (and reversed card)', 'Cloze']
        """
        return self._invoke("modelNames")

    def get_model_names_and_ids(self) -> Dict[str, int]:
        """取得所有模型名稱及其對應的 ID。

        Returns:
            字典，鍵為模型名稱（str），值為模型 ID（int）。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_model_names_and_ids()
            {'Basic': 1483883011648, 'Cloze': 1483883011630}
        """
        return self._invoke("modelNamesAndIds")

    def get_model_field_names(self, model_name: str) -> List[str]:
        """取得指定模型的所有欄位名稱。

        Args:
            model_name: 模型名稱。

        Returns:
            欄位名稱字串列表，按順序排列。

        Raises:
            AnkiConnectError: 模型不存在或 API 請求失敗時。

        Example:
            >>> ac.get_model_field_names('Basic')
            ['Front', 'Back']
        """
        return self._invoke("modelFieldNames", modelName=model_name)

    # ========================================================================
    # 雜項操作（Miscellaneous Actions）
    # ========================================================================

    def get_version(self) -> int:
        """取得 AnkiConnect API 版本號。

        Returns:
            API 版本號整數，當前為 6。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_version()
            6
        """
        return self._invoke("version")

    def sync(self) -> None:
        """同步本地 Anki 集合至 AnkiWeb。

        Raises:
            AnkiConnectError: 同步失敗時。

        Example:
            >>> ac.sync()
        """
        self._invoke("sync")

    def request_permission(self) -> Dict[str, Any]:
        """請求使用 AnkiConnect API 的權限。

        此方法不需要 API 金鑰，且接受來自任何來源的請求。
        首次從不受信任的來源呼叫時，Anki 會顯示彈窗詢問使用者是否允許。

        Returns:
            權限資訊字典，包含：
            - permission (str): 'granted' 或 'denied'
            - requireApiKey (bool): 是否需要 API 金鑰（僅在 granted 時包含）
            - version (int): API 版本號（僅在 granted 時包含）

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> result = ac.request_permission()
            >>> print(result['permission'])
            'granted'
        """
        return self._invoke("requestPermission")

    def get_profiles(self) -> List[str]:
        """取得所有 Anki 使用者設定檔名稱。

        Returns:
            設定檔名稱字串列表。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> ac.get_profiles()
            ['User 1']
        """
        return self._invoke("getProfiles")

    def multi(self, actions: List[Dict[str, Any]]) -> List[Any]:
        """在單一請求中執行多個 API 動作。

        Args:
            actions: API 動作列表，每個元素為包含 action 和可選 params 的字典。
                     範例：
                     [
                         {'action': 'deckNames'},
                         {'action': 'version'},
                         {'action': 'findNotes', 'params': {'query': 'deck:Default'}},
                     ]

        Returns:
            結果列表，每個元素對應一個動作的回傳值。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> results = ac.multi([
            ...     {'action': 'deckNames'},
            ...     {'action': 'version'},
            ... ])
            >>> print(results)
            [['Default'], 6]
        """
        return self._invoke("multi", actions=actions)
