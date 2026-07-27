from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_temperature: float = 0.7

    initial_cash: float = 1_000_000.0

    schedule_enabled: bool = True
    daily_decision_time: str = "14:35"
    monitor_interval_minutes: int = 15

    # 自动选股
    stock_select_enabled: bool = True
    stock_select_time: str = "14:05"
    pool_max: int = 8

    # 盘中复审阈值
    take_profit_review_pct: float = 0.15
    stop_loss_review_pct: float = -0.08
    deep_loss_pct: float = -0.15

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
