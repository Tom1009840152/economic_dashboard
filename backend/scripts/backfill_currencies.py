"""一次性回补所有汇率指标的历史数据到 2000-01-01。

用法（在 backend 目录下，激活 venv 后）：
    python -m scripts.backfill_currencies          # 回补 BOC_CURRENCIES 里所有货币
    python -m scripts.backfill_currencies EURCNY JPYCNY   # 只回补指定的几个

常规的定时刷新只拉最近 90 天（见 app/fetchers/akshare_source.py），
更早的历史靠这个脚本跑一次性把 2000 年至今的数据灌进数据库，之后就一直在库里了。
"""

import logging
import sys

from app.db import SessionLocal
from app.fetchers.akshare_source import BOC_CURRENCIES, backfill_boc_currency
from app.services.indicator_service import ensure_indicators_seeded, upsert_points

logging.basicConfig(level=logging.INFO)


def main():
    codes = sys.argv[1:] or list(BOC_CURRENCIES.keys())

    db = SessionLocal()
    try:
        ensure_indicators_seeded(db)
        for code in codes:
            cn_symbol, scale = BOC_CURRENCIES[code]
            df = backfill_boc_currency(cn_symbol, scale)
            count = upsert_points(db, code, df)
            print(f"{code} 回补完成：{count} 条")
    finally:
        db.close()


if __name__ == "__main__":
    main()
