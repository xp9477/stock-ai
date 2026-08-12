from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_temperature: float = 0.7

    # Canary 授权账户。用户总本金不进入策略上下文或数据库。
    initial_cash: float = 100_000.0

    schedule_enabled: bool = True
    daily_decision_time: str = "16:00"
    monitor_interval_minutes: int = 5

    # 自动选股 / 候选池（确定性资格筛选后上限 30）
    stock_select_enabled: bool = True
    stock_select_time: str = "15:30"
    pool_max: int = 30

    # 盘中只告警/复审，任何卖出都必须人工确认。
    take_profit_review_pct: float = 0.15
    stop_loss_review_pct: float = -0.08
    deep_loss_pct: float = -0.15
    deep_loss_auto_execute: bool = False
    shallow_line_alert_only: bool = True

    db_path: str = "stock_ai.db"

    # External broker SDKs run in an isolated read-only bridge.  The web app
    # consumes only its normalized, atomically-written simulation snapshot.
    broker_snapshot_path: str = "/data/broker/emt_snapshot.json"
    broker_snapshot_max_age_seconds: int = 60
    # Accepted personal-capital boundary. A broker query can be technically
    # complete yet still be the vendor's huge seeded demo portfolio; that
    # state must never become decision reference data.
    broker_snapshot_max_total_asset: float = 400_000.0
    broker_reference_required: bool = False
    broker_snapshot_initial_equity: float = 200_000.0

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

    # 历史验证证据门槛（字段名保留 race_* 兼容旧环境变量）
    race_min_trade_days: int = 60
    race_min_closed_trades: int = 100

    # 硬性风控参数
    max_position_pct: float = 0.30      # 单票市值不超过总资产 30%
    max_buy_cash_pct: float = 0.50      # 单次买入不超过可用资金 50%
    max_total_position_pct: float = 0.80  # 10 万授权账户中股票敞口不超过 8 万
    stop_loss_alert_pct: float = -0.10  # 亏损超 10% 提示止损

    # 交易费用
    commission_rate: float = 0.00025
    commission_min: float = 5.0
    stamp_tax_rate: float = 0.0005      # 卖出印花税
    transfer_fee_rate: float = 0.00001  # 过户费


settings = Settings()
