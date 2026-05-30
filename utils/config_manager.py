"""
設定管理員 (Configuration Manager) 模組。

職責：
1. 使用 pydantic-settings 從 `.env` 檔案載入環境變數。
2. 若缺少必填環境變數會自動拋出驗證錯誤。
3. 負責初始化與配置全域 Logging 日誌。
"""

import logging
import sys
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


from pathlib import Path

# 動態計算專案根目錄下的 .env 檔案位置，避免執行路徑不同導致找不到
_env_path = Path(__file__).resolve().parent.parent / ".env"

class ConfigManager(BaseSettings):
    """應用程式配置管理類別。

    透過 pydantic-settings 讀取環境變數，並強制進行型別與必填項檢查。

    Attributes:
        llm_api_key (str): OpenAI 共容 API 金鑰 (例如 Gemini API Key)。
        llm_base_url (str): OpenAI 共容 API 端點 URL。
        llm_model_name (str): 預定使用的 LLM 模型名稱 (例如 gemini-1.5-flash)。
        anki_connect_url (str): AnkiConnect 的本地端點。預設為 http://127.0.0.1:8765。
        log_level (str): 應用程式的預設日誌層級，預設為 INFO。
    """
    model_config = SettingsConfigDict(
        env_file=str(_env_path),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    llm_api_key: str = Field(..., description="LLM 的 API 金鑰")
    llm_base_url: str = Field(..., description="LLM 的自訂連線端點")
    llm_model_name: str = Field(..., description="LLM 預設預測用的模型名稱")
    anki_connect_url: str = Field(
        default="http://127.0.0.1:8765",
        description="AnkiConnect 本地端點"
    )
    cf_access_client_id: str | None = Field(default=None, description="Cloudflare Access Client ID")
    cf_access_client_secret: str | None = Field(default=None, description="Cloudflare Access Client Secret")
    log_level: str = Field(default="INFO", description="系統日誌層級 (DEBUG/INFO/WARNING/ERROR)")

    def setup_logging(self) -> None:
        """設定全域日誌 (Global Logging)。

        設定標準輸出格式與指定層級。若已配置過 root logger，將覆蓋舊的 handler，確保一致性。
        """
        level: int = getattr(logging, self.log_level.upper(), logging.INFO)

        # 設定 root logger
        root_logger: logging.Logger = logging.getLogger()
        root_logger.setLevel(level)

        # 移除已存在的 handlers 避免重複輸出
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        handler_sh: logging.StreamHandler = logging.StreamHandler(sys.stdout)
        handler_sh.setLevel(level)
        formatter: logging.Formatter = logging.Formatter(
            fmt="%(asctime)s | %(name)-15s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler_sh.setFormatter(formatter)
        root_logger.addHandler(handler_sh)

        # 本模組內測試記錄
        logger = logging.getLogger(__name__)
        logger.info("系統日誌初始化完成，層級: %s", self.log_level)


# 預設提供一個全域實例，避免重複解析
try:
    config = ConfigManager()
    config.setup_logging()
except Exception as e:
    # 這裡刻意捕捉 Exception 打印錯誤原因，避免只看到 Pydantic ValidationError 的龐大錯誤堆棧而不知所云
    print(f"環境變數配置錯誤或缺失: {e}", file=sys.stderr)
    raise
