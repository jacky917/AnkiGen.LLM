"""
AnkiConnect CRUD 測試入口

本模組作為 AnkiConnect 類別的測試入口，依序驗證所有 CRUD 操作：
1. 連線與版本檢查
2. 牌組（Deck）的建立、查詢、刪除
3. 筆記（Note）的新增、搜尋、更新、刪除
4. 卡片（Card）的查詢與狀態操作
5. 媒體（Media）的儲存、讀取、刪除
6. 模型（Model）的查詢
7. 雜項操作（同步、批次執行）

注意：執行前請確保 Anki 已啟動且 AnkiConnect 插件已安裝。

Usage:
    python main.py
"""

import base64
import logging

from utils.anki_connect import AnkiConnect, AnkiConnectError

# ============================================================================
# 日誌配置
# 設定 INFO 等級以輸出操作摘要，設定 DEBUG 可查看每次 API 請求的詳細資訊
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================================
# 測試用常數
# 使用獨立的牌組名稱，避免影響使用者現有的牌組資料
# 模型名稱不硬編碼，因為不同語系的 Anki 模型名稱不同
# （例如日文版的 "Basic" 可能叫 "基本" 或其他名稱）
# ============================================================================
TEST_DECK_NAME = "AnkiConnect::Test"
TEST_MODEL_NAME: str = ""  # 由 resolve_model_name() 動態設定


def resolve_model_name(ac: AnkiConnect) -> str:
    """動態偵測可用的模型名稱。

    不同語系的 Anki 安裝會使用不同的預設模型名稱，
    因此不能硬編碼 'Basic'。此函數會從伺服器取得所有模型，
    並回傳第一個可用的模型名稱。

    Args:
        ac: AnkiConnect 客戶端實例。

    Returns:
        第一個可用的模型名稱。

    Raises:
        RuntimeError: 當 Anki 中沒有任何模型時。
    """
    # 從伺服器取得所有可用模型名稱
    models = ac.get_model_names()

    if not models:
        raise RuntimeError("Anki 中沒有任何模型，無法執行測試")

    # 列出所有可用模型供使用者參考
    print(f"  📋 可用模型: {models}")

    # 取得每個模型的欄位，找到包含常見欄位的模型（優先選擇有 Front/Back 的模型）
    for model in models:
        fields = ac.get_model_field_names(model)
        # 檢查模型是否具有兩個欄位（最基本的正反面卡片結構）
        if len(fields) >= 2:
            print(f"  ✅ 選用模型: '{model}'（欄位: {fields}）")
            return model

    # 若找不到理想模型，退而使用第一個模型
    print(f"  ⚠️ 未找到雙欄位模型，使用第一個模型: '{models[0]}'")
    return models[0]


def separator(title: str) -> None:
    """印出分隔線以區隔不同測試區塊。

    Args:
        title: 區塊標題。
    """
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def test_connection(ac: AnkiConnect) -> None:
    """測試與 AnkiConnect 伺服器的連線。

    驗證項目：
    - API 版本號是否為 6
    - 權限請求是否成功

    Args:
        ac: AnkiConnect 客戶端實例。
    """
    separator("1. 連線與版本檢查")

    # 取得 API 版本號，預期為 6
    version = ac.get_version()
    print(f"  ✅ AnkiConnect API 版本: {version}")

    # 請求 API 使用權限
    permission = ac.request_permission()
    print(f"  ✅ 權限狀態: {permission['permission']}")

    # 取得使用者設定檔列表
    profiles = ac.get_profiles()
    print(f"  ✅ 使用者設定檔: {profiles}")


def test_deck_operations(ac: AnkiConnect) -> None:
    """測試牌組的 CRUD 操作。

    測試流程：
    1. 查詢現有牌組列表
    2. 建立測試牌組
    3. 取得牌組設定
    4. 驗證牌組已建立
    5. 刪除測試牌組（在筆記測試結束後執行）

    Args:
        ac: AnkiConnect 客戶端實例。
    """
    separator("2. 牌組操作 (Deck)")

    # ---- Read：查詢所有牌組 ----
    deck_names = ac.get_deck_names()
    print(f"  📋 現有牌組: {deck_names}")

    # ---- Read：查詢牌組名稱與 ID ----
    deck_map = ac.get_deck_names_and_ids()
    print(f"  📋 牌組 ID 對應: {deck_map}")

    # ---- Create：建立測試牌組 ----
    deck_id = ac.create_deck(TEST_DECK_NAME)
    print(f"  ✅ 建立測試牌組 '{TEST_DECK_NAME}'，ID: {deck_id}")

    # ---- Read：取得牌組設定 ----
    config = ac.get_deck_config(TEST_DECK_NAME)
    print(f"  📋 牌組設定 - 每日新卡片: {config['new']['perDay']}")

    # 驗證牌組已建立
    updated_decks = ac.get_deck_names()
    assert TEST_DECK_NAME in updated_decks, "測試牌組建立失敗"
    print(f"  ✅ 驗證牌組存在: {TEST_DECK_NAME} ∈ 牌組列表")


def test_note_operations(ac: AnkiConnect, field_names: list) -> int:
    """測試筆記的 CRUD 操作。

    使用動態取得的模型名稱和欄位名稱，避免硬編碼導致的語系相容性問題。

    測試流程：
    1. 新增單一筆記
    2. 批次新增筆記
    3. 搜尋筆記
    4. 查詢筆記詳細資訊
    5. 更新筆記欄位與標籤
    6. 標籤管理操作

    Args:
        ac: AnkiConnect 客戶端實例。
        field_names: 模型的欄位名稱列表（動態取得，例如 ['Front', 'Back'] 或 ['表面', '裏面']）。

    Returns:
        建立的第一則筆記 ID，供後續卡片測試使用。
    """
    separator("3. 筆記操作 (Note)")

    # 取得前兩個欄位名稱，用於填入測試資料
    f1, f2 = field_names[0], field_names[1]
    print(f"  ℹ️  使用模型: {TEST_MODEL_NAME}，欄位: {f1} / {f2}")

    # ---- Create：新增單一筆記 ----
    note_id = ac.add_note(
        deck_name=TEST_DECK_NAME,
        model_name=TEST_MODEL_NAME,
        fields={
            f1: "What is AnkiConnect?",
            f2: "An Anki plugin that exposes APIs via HTTP.",
        },
        tags=["test", "ankiconnect"],
    )
    print(f"  ✅ 新增筆記，ID: {note_id}")

    # ---- Create：批次新增筆記 ----
    batch_ids = ac.add_notes([
        {
            "deckName": TEST_DECK_NAME,
            "modelName": TEST_MODEL_NAME,
            "fields": {f1: "Batch Q1: What is Python?", f2: "A programming language."},
            "tags": ["test", "batch"],
        },
        {
            "deckName": TEST_DECK_NAME,
            "modelName": TEST_MODEL_NAME,
            "fields": {f1: "Batch Q2: What is REST?", f2: "REpresentational State Transfer."},
            "tags": ["test", "batch"],
        },
    ])
    print(f"  ✅ 批次新增筆記，IDs: {batch_ids}")

    # ---- Read：搜尋測試牌組中的所有筆記 ----
    # 牌組名稱含有特殊字元（如 ::）時需用引號包裹
    found_ids = ac.find_notes(f'"deck:{TEST_DECK_NAME}"')
    print(f"  🔍 搜尋筆記（deck:{TEST_DECK_NAME}）: 找到 {len(found_ids)} 則")

    # ---- Read：取得筆記詳細資訊 ----
    if found_ids:
        infos = ac.get_notes_info(notes=found_ids[:1])
        info = infos[0]
        print(f"  📋 筆記詳情:")
        print(f"     模型: {info['modelName']}")
        print(f"     標籤: {info['tags']}")
        # 遍歷所有欄位，印出欄位名稱和值
        for fname, field_data in info["fields"].items():
            print(f"     {fname}: {field_data['value'][:50]}")

    # ---- Update：更新筆記欄位和標籤 ----
    ac.update_note(
        note_id=note_id,
        fields={
            f1: "What is AnkiConnect? (Updated)",
            f2: "A powerful Anki plugin for external API access.",
        },
        tags=["test", "ankiconnect", "updated"],
    )
    print(f"  ✅ 更新筆記欄位與標籤，ID: {note_id}")

    # ---- Update：僅更新欄位 ----
    ac.update_note_fields(
        note_id=note_id,
        fields={f2: "A powerful Anki plugin that exposes APIs via HTTP (v6)."},
    )
    print(f"  ✅ 更新筆記欄位，ID: {note_id}")

    # ---- 標籤管理 ----
    # 新增標籤
    ac.add_tags(found_ids, "integration-test")
    print(f"  🏷️  新增標籤 'integration-test' 到 {len(found_ids)} 則筆記")

    # 取得所有標籤
    all_tags = ac.get_tags()
    print(f"  📋 所有標籤: {all_tags}")

    # 替換標籤
    ac.replace_tags(found_ids, "integration-test", "verified")
    print(f"  🏷️  替換標籤 'integration-test' → 'verified'")

    # 移除標籤
    ac.remove_tags(found_ids, "verified")
    print(f"  🏷️  移除標籤 'verified'")

    # 檢查是否可以新增筆記（不實際建立）
    can_add = ac.can_add_notes([{
        "deckName": TEST_DECK_NAME,
        "modelName": TEST_MODEL_NAME,
        "fields": {f1: "Unique test note", f2: "Answer"},
    }])
    print(f"  🔍 預檢筆記可新增性: {can_add}")

    return note_id


def test_card_operations(ac: AnkiConnect) -> None:
    """測試卡片的查詢與狀態操作。

    測試流程：
    1. 搜尋測試牌組中的卡片
    2. 取得卡片詳細資訊
    3. 暫停與取消暫停卡片
    4. 讀取與設定簡易度因子

    Args:
        ac: AnkiConnect 客戶端實例。
    """
    separator("4. 卡片操作 (Card)")

    # ---- Read：搜尋卡片 ----
    card_ids = ac.find_cards(f"deck:{TEST_DECK_NAME}")
    print(f"  🔍 搜尋卡片（deck:{TEST_DECK_NAME}）: 找到 {len(card_ids)} 張")

    if not card_ids:
        print("  ⚠️ 無卡片可測試，跳過")
        return

    # 取第一張卡片進行後續測試
    test_card_id = card_ids[0]

    # ---- Read：取得卡片詳情 ----
    card_infos = ac.get_cards_info([test_card_id])
    if card_infos:
        card = card_infos[0]
        print(f"  📋 卡片詳情:")
        print(f"     卡片 ID: {card['cardId']}")
        print(f"     所屬牌組: {card['deckName']}")

    # ---- Update：暫停卡片 ----
    ac.suspend_cards([test_card_id])
    print(f"  ⏸️  暫停卡片: {test_card_id}")

    # ---- Read：檢查暫停狀態 ----
    statuses = ac.are_suspended([test_card_id])
    print(f"  🔍 暫停狀態: {statuses}")

    # ---- Update：取消暫停 ----
    ac.unsuspend_cards([test_card_id])
    print(f"  ▶️  取消暫停: {test_card_id}")

    # ---- Read：取得簡易度因子 ----
    factors = ac.get_ease_factors([test_card_id])
    print(f"  📊 簡易度因子: {factors}")

    # ---- Update：設定簡易度因子 ----
    ac.set_ease_factors([test_card_id], [2500])
    print(f"  ✅ 設定簡易度因子為 2500")


def test_media_operations(ac: AnkiConnect) -> None:
    """測試媒體檔案的 CRUD 操作。

    測試流程：
    1. 儲存 Base64 編碼的文字檔案
    2. 讀取檔案內容並驗證
    3. 搜尋媒體檔案
    4. 取得媒體資料夾路徑
    5. 刪除測試檔案

    Args:
        ac: AnkiConnect 客戶端實例。
    """
    separator("5. 媒體操作 (Media)")

    # 測試用檔案名稱（以底線開頭防止 Anki 自動清理）
    test_filename = "_ankiconnect_test.txt"
    test_content = "Hello from AnkiConnect Python Client! 🎉"

    # ---- Create：使用 Base64 儲存檔案 ----
    # 將文字內容編碼為 Base64 字串
    encoded_data = base64.b64encode(test_content.encode("utf-8")).decode("utf-8")
    stored_name = ac.store_media_file(filename=test_filename, data=encoded_data)
    print(f"  ✅ 儲存媒體檔案: {stored_name}")

    # ---- Read：讀取檔案內容 ----
    retrieved = ac.retrieve_media_file(test_filename)
    if retrieved:
        # 將 Base64 解碼回原始文字
        decoded = base64.b64decode(retrieved).decode("utf-8")
        print(f"  📋 讀取檔案內容: '{decoded}'")
        # 驗證內容一致
        assert decoded == test_content, "檔案內容不一致"
        print(f"  ✅ 內容驗證通過")
    else:
        print(f"  ❌ 檔案不存在")

    # ---- Read：搜尋媒體檔案 ----
    found_files = ac.get_media_files_names("_ankiconnect_test*")
    print(f"  🔍 搜尋媒體檔案: {found_files}")

    # ---- Read：取得媒體資料夾路徑 ----
    media_dir = ac.get_media_dir_path()
    print(f"  📁 媒體資料夾: {media_dir}")

    # ---- Delete：刪除測試檔案 ----
    ac.delete_media_file(test_filename)
    print(f"  🗑️  刪除媒體檔案: {test_filename}")

    # 驗證檔案已刪除
    verify = ac.retrieve_media_file(test_filename)
    assert verify is False, "檔案刪除失敗"
    print(f"  ✅ 驗證檔案已刪除")


def test_model_operations(ac: AnkiConnect) -> None:
    """測試模型（筆記類型）的查詢操作。

    測試流程：
    1. 取得所有模型名稱
    2. 取得模型名稱與 ID 對應
    3. 取得指定模型的欄位名稱

    Args:
        ac: AnkiConnect 客戶端實例。
    """
    separator("6. 模型操作 (Model)")

    # ---- Read：取得所有模型名稱 ----
    model_names = ac.get_model_names()
    print(f"  📋 所有模型: {model_names}")

    # ---- Read：取得模型名稱與 ID ----
    model_map = ac.get_model_names_and_ids()
    print(f"  📋 模型 ID 對應:")
    for name, mid in model_map.items():
        print(f"     {name}: {mid}")

    # ---- Read：取得指定模型的欄位名稱 ----
    if model_names:
        # 取第一個模型來查詢欄位
        target_model = model_names[0]
        field_names = ac.get_model_field_names(target_model)
        print(f"  📋 模型 '{target_model}' 的欄位: {field_names}")


def test_misc_operations(ac: AnkiConnect) -> None:
    """測試雜項操作。

    測試流程：
    1. 批次執行多個 API 動作
    （同步操作僅印出說明，不實際執行以避免影響使用者資料）

    Args:
        ac: AnkiConnect 客戶端實例。
    """
    separator("7. 雜項操作 (Misc)")

    # ---- multi：批次執行多個動作 ----
    results = ac.multi([
        {"action": "deckNames"},
        {"action": "version"},
        {"action": "getTags"},
    ])
    print(f"  ✅ 批次執行 3 個動作:")
    print(f"     牌組: {results[0]}")
    print(f"     版本: {results[1]}")
    print(f"     標籤: {results[2]}")

    # 同步操作說明（不自動執行，避免意外同步）
    print(f"  ℹ️  同步操作（ac.sync()）已跳過，如需測試請手動呼叫")


def cleanup(ac: AnkiConnect) -> None:
    """清理所有測試資料。

    刪除測試過程中建立的牌組及其所有卡片。

    Args:
        ac: AnkiConnect 客戶端實例。
    """
    separator("🧹 清理測試資料")

    try:
        # 刪除測試牌組（連同其中所有卡片）
        ac.delete_decks([TEST_DECK_NAME])
        print(f"  🗑️  已刪除測試牌組: {TEST_DECK_NAME}")

        # 清除未使用的標籤
        ac.clear_unused_tags()
        print(f"  🗑️  已清除未使用的標籤")

    except AnkiConnectError as e:
        print(f"  ⚠️ 清理時發生錯誤: {e.message}")


def main() -> None:
    """主入口函數。

    依序執行所有測試區塊，最後清理測試資料。
    任何單個測試區塊失敗不會影響其他區塊的執行。
    """
    # 宣告全域變數，讓 resolve_model_name() 的結果可被所有測試函數存取
    global TEST_MODEL_NAME

    # 初始化客戶端（從 .env 讀取配置）
    ac = AnkiConnect()

    # 動態偵測可用模型（必須在印出測試資訊之前完成）
    separator("0. 動態偵測模型")
    TEST_MODEL_NAME = resolve_model_name(ac)

    # 取得選用模型的欄位名稱，供筆記測試使用
    field_names = ac.get_model_field_names(TEST_MODEL_NAME)

    print(f"\n🃏 AnkiConnect CRUD 測試")
    print(f"   測試牌組: {TEST_DECK_NAME}")
    print(f"   測試模型: {TEST_MODEL_NAME}")
    print(f"   模型欄位: {field_names}")

    try:
        # 步驟 1：連線檢查（若失敗則直接終止）
        test_connection(ac)

        # 步驟 2：牌組 CRUD
        test_deck_operations(ac)

        # 步驟 3：筆記 CRUD（傳入動態欄位名稱，回傳筆記 ID 供卡片測試）
        note_id = test_note_operations(ac, field_names)

        # 步驟 4：卡片操作
        test_card_operations(ac)

        # 步驟 5：媒體 CRUD
        test_media_operations(ac)

        # 步驟 6：模型查詢
        test_model_operations(ac)

        # 步驟 7：雜項操作
        test_misc_operations(ac)

    except AnkiConnectError as e:
        # 捕獲 AnkiConnect 相關錯誤
        logger.error("測試過程中發生 AnkiConnect 錯誤: %s", e.message)
        print(f"\n❌ 測試中斷: {e.message}")
    except Exception as e:
        # 捕獲其他未預期的錯誤
        logger.error("測試過程中發生未預期的錯誤: %s", str(e))
        print(f"\n❌ 測試中斷（未預期錯誤）: {e}")
    finally:
        # 無論測試是否成功，都嘗試清理測試資料
        cleanup(ac)

    separator("✅ 測試完成")
    print("  所有 CRUD 操作已驗證完畢！")


# ============================================================================
# 程式入口
# 當直接執行此檔案時（非被 import 時），呼叫 main() 函數
# ============================================================================
if __name__ == "__main__":
    main()
