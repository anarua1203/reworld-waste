# tools.py

import pandas as pd

_MOCK_TABLES: dict[str, pd.DataFrame] = {
    "sales": pd.DataFrame(
        {
            "order_id": [1001, 1002, 1003, 1004, 1005],
            "product": ["Widget A", "Widget B", "Gadget X", "Widget A", "Gadget Y"],
            "region": ["US-West", "US-East", "EU", "US-West", "APAC"],
            "quantity": [10, 5, 8, 12, 3],
            "unit_price": [29.99, 49.99, 99.99, 29.99, 149.99],
            "total": [299.90, 249.95, 799.92, 359.88, 449.97],
        }
    ),
    "inventory": pd.DataFrame(
        {
            "sku": ["WA-001", "WB-002", "GX-003", "GY-004"],
            "product": ["Widget A", "Widget B", "Gadget X", "Gadget Y"],
            "stock": [250, 80, 45, 12],
            "warehouse": ["PDX", "JFK", "AMS", "SIN"],
            "reorder_point": [50, 20, 10, 5],
        }
    ),
    "customers": pd.DataFrame(
        {
            "customer_id": [101, 102, 103, 104],
            "name": ["Acme Corp", "Globex Inc", "Initech", "Umbrella Ltd"],
            "tier": ["Gold", "Silver", "Bronze", "Gold"],
            "region": ["US-West", "US-East", "EU", "APAC"],
            "annual_spend": [125000.00, 48000.00, 12000.00, 210000.00],
        }
    ),
}

_DEFAULT_TABLE = "sales"

# Mock industry revenue data — replace with real Snowflake query later
_MOCK_INDUSTRY_REVENUE = pd.DataFrame(
    {
        "industry": [
            "Technology",
            "Healthcare",
            "Finance",
            "Retail",
            "Manufacturing",
            "Energy",
            "Telecommunications",
            "Real Estate",
        ],
        "avg_annual_revenue_usd": [
            4_200_000_000,
            1_850_000_000,
            3_100_000_000,
            980_000_000,
            1_450_000_000,
            2_750_000_000,
            2_100_000_000,
            620_000_000,
        ],
        "avg_revenue_growth_pct": [12.4, 7.8, 6.2, 3.1, 4.5, 5.9, 3.8, 6.7],
        "sample_size": [320, 215, 180, 540, 410, 95, 130, 260],
        "data_year": [2024] * 8,
    }
)
