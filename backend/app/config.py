from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.7

    initial_cash: float = 1_000_000.0

    schedule_enabled: bool = True
    schedule_times: str = "10:00,11:00,13:30,14:30"

    db_path: str = "stock_ai.db"

    # 硬性风控参数
    max_position_pct: float = 0.30      # 单票市值不超过总资产 30%
    max_buy_cash_pct: float = 0.50      # 单次买入不超过可用资金 50%
    max_total_position_pct: float = 0.90  # 总仓位不超过 90%
    stop_loss_alert_pct: float = -0.10  # 亏损超 10% 提示止损

    # 交易费用
    commission_rate: float = 0.00025
    commission_min: float = 5.0
    stamp_tax_rate: float = 0.0005      # 卖出印花税
    transfer_fee_rate: float = 0.00001  # 过户费


settings = Settings()
