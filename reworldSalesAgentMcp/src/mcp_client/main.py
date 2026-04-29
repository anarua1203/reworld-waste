# main.py

import logging

import pandas as pd
from mcp.server.fastmcp import FastMCP

from tools import _MOCK_INDUSTRY_REVENUE

log = logging.getLogger(__name__)


mcp = FastMCP("SalesMCP", host="0.0.0.0", stateless_http=True)


@mcp.tool()
def get_average_industry_revenue(industry: str) -> str:
"""
Return average annual revenue benchmarks for a given industry.

Queries Snowflake for industry-level revenue statistics including average
annual revenue, year-over-year growth percentage, and sample size.

Args:
industry: Name of the industry to look up (e.g. "Technology", "Healthcare",
"Finance", "Retail", "Manufacturing", "Energy",
"Telecommunications", "Real Estate").
Case-insensitive. If not found, all industries are returned.

Returns:
JSON-serialised records with columns:
industry, avg_annual_revenue_usd, avg_revenue_growth_pct,
sample_size, data_year
"""
log.info("tool invoked industry=%s", industry)
df = _fetch_industry_revenue_from_snowflake(industry)
result = df.to_json(orient="records", indent=2)
log.info("returning %d row(s)", len(df))
return result

def _fetch_industry_revenue_from_snowflake(industry: str) -> pd.DataFrame:
"""
Fetch average revenue data for an industry from Snowflake.

TODO: Replace this mock with real Snowflake logic, e.g.:
cur.execute(
"SELECT industry, avg_annual_revenue_usd, avg_revenue_growth_pct, "
"sample_size, data_year "
"FROM industry_benchmarks "
"WHERE LOWER(industry) = LOWER(%s)",
(industry,)
)
return cur.fetch_pandas_all()
"""
log.info("mock industry revenue fetch industry=%s", industry)
mask = _MOCK_INDUSTRY_REVENUE["industry"].str.lower() == industry.strip().lower()
result = _MOCK_INDUSTRY_REVENUE[mask]
if result.empty:
# Return all rows when the industry isn't found so the caller can see options
log.warning("industry not found, returning all industries industry=%s", industry)
return _MOCK_INDUSTRY_REVENUE.copy()
return result.reset_index(drop=True)


if __name__ == "__main__":
mcp.run(transport="streamable-http")