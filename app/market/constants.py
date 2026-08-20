"""A股市场常量定义"""

# 交易时段
PRE_MARKET_OPEN = "09:15"       # 集合竞价开始
CALL_AUCTION_END = "09:25"      # 集合竞价结束
MORNING_OPEN = "09:30"          # 上午连续竞价开始
MORNING_CLOSE = "11:30"         # 上午收盘
AFTERNOON_OPEN = "13:00"        # 下午连续竞价开始
AFTERNOON_CLOSE = "15:00"       # 下午收盘

# 涨跌停限制
PRICE_LIMIT_MAIN = 0.10         # 主板 ±10%
PRICE_LIMIT_CHI_NEXT = 0.20     # 创业板 ±20%
PRICE_LIMIT_STAR = 0.20         # 科创板 ±20%
PRICE_LIMIT_ST = 0.05           # ST 股票 ±5%
PRICE_LIMIT_NEW = 0.44          # 新股上市首日涨幅 44%

# 交易规则
MIN_LOT_SIZE = 100              # 最小交易单位 (1手 = 100股)
T_PLUS_DAYS = 1                 # T+1 交易

# 市场代码前缀
MARKET_PREFIXES = {
    "60": "沪市主板",
    "00": "深市主板",
    "30": "创业板",
    "68": "科创板",
    "8":  "北交所",
    "4":  "北交所",
}
