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

    # 自动选股 / 共享股池（赛马宇宙上限 30）
    stock_select_enabled: bool = True
    stock_select_time: str = "14:05"
    pool_max: int = 30

    # 盘中：浅线仅告警，深亏强制自动砍（分层 3）
    take_profit_review_pct: float = 0.15
    stop_loss_review_pct: float = -0.08
    deep_loss_pct: float = -0.15
    deep_loss_auto_execute: bool = True
    shallow_line_alert_only: bool = True

    db_path: str = "stock_ai.db"

    # 行情/财务主源：同花顺扶摇（唯一；不够再用 Tushare，不做免费源降级）
    fuyao_api_key: str = ""
    # 可选备份，默认不用
    tushare_token: str = ""

    # S2 因子组合：周频再平衡，综合分前 N 等权
    factor_rebalance: str = "W-MON"  # pandas 周频锚到周一
    factor_top_n: int = 10
    factor_lookback_short: int = 5
    factor_lookback_mid: int = 20
    factor_vol_window: int = 20

    # 赛马验收门槛
    race_min_trade_days: int = 60
    race_min_closed_trades: int = 100

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
