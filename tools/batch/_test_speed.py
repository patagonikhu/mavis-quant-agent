import duckdb, time
t0 = time.time()
df = duckdb.execute("""
SELECT ts_code, trade_date, close, high
FROM read_parquet('data/history/daily/*.parquet')
WHERE trade_date >= '20220101'
""").df()
print(f"rows: {len(df):,}, time: {time.time()-t0:.1f}s, codes: {df['ts_code'].nunique()}")
