"""Populate a running Escal API (default http://localhost:8100) with demo
data so you can immediately see revenue, ad spend and True ROAS.

Usage:
    python scripts/seed_demo.py
"""

import random
import uuid
from datetime import datetime, timedelta, timezone

import requests

BASE_URL = "http://localhost:8100"


def main():
    email = f"demo-{uuid.uuid4().hex[:8]}@escal.dev"
    auth = requests.post(
        f"{BASE_URL}/auth/register",
        json={"account_name": "Demo Account", "email": email, "password": "demo-password-123"},
    ).json()
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("registered:", email)

    account = requests.get(f"{BASE_URL}/accounts/me", headers=headers).json()
    print("account:", account["id"])

    store = requests.post(
        f"{BASE_URL}/stores",
        headers=headers,
        json={
            "name": "Demo Store",
            "platform": "shopify",
            "currency": "USD",
            "timezone": "UTC",
        },
    ).json()
    store_id = store["id"]
    print("store:", store_id)

    products = requests.put(
        f"{BASE_URL}/stores/{store_id}/products",
        headers=headers,
        json=[
            {"external_id": "sku-1", "sku": "SKU1", "title": "T-Shirt", "cogs": 5.0, "shipping_cost": 2.0},
            {"external_id": "sku-2", "sku": "SKU2", "title": "Hoodie", "cogs": 12.0, "shipping_cost": 3.0},
        ],
    ).json()
    print("products:", [p["title"] for p in products])

    # A small repeat-customer pool (rather than a unique email per order) so
    # the LTV-by-cohort widget has actual repeat-purchase data to show. Each
    # customer's first order is pinned to a fixed acquisition day (not just
    # picked at random per order) — a customer can only appear on/after
    # their own acquisition day, spread across ~90 days so demo data covers
    # 2-3 distinct cohort months instead of every customer's *true* first
    # order (the minimum across ~100+ random appearances) collapsing onto
    # the single oldest day by sheer chance.
    demo_customers = [f"demo-customer-{i}@example.com" for i in range(6)]
    acquisition_offsets = [87, 87, 50, 50, 12, 12]

    now = datetime.now(timezone.utc)
    orders = []
    for day_offset in range(90):
        day = now - timedelta(days=day_offset)
        eligible_customers = [c for c, offset in zip(demo_customers, acquisition_offsets) if day_offset <= offset]
        for _ in range(random.randint(3, 10)):
            gross = round(random.uniform(20, 150), 2)
            cogs = round(gross * 0.25, 2)
            order = {
                "time": (day - timedelta(hours=random.randint(0, 23))).isoformat(),
                "order_id": f"order-{day_offset}-{random.randint(1000, 9999)}",
                "gross_amount": gross,
                "discounts": 0.0,
                "shipping_fee": 4.99,
                "payment_gateway_fee": round(gross * 0.029 + 0.30, 2),
                "cogs_total": cogs,
                "currency": "USD",
                "attribution_utm_source": random.choice(["meta", "google", "organic"]),
                "attribution_utm_campaign": "demo-campaign",
            }
            if eligible_customers:
                order["customer_email"] = random.choice(eligible_customers)
            orders.append(order)

    # Force one order exactly on each customer's acquisition day — the
    # random per-day assignment above makes it likely but not guaranteed,
    # and a reliable demo needs every cohort month to actually show up.
    for email, offset in zip(demo_customers, acquisition_offsets):
        day = now - timedelta(days=offset)
        gross = round(random.uniform(20, 150), 2)
        orders.append(
            {
                "time": (day - timedelta(minutes=random.randint(0, 59))).isoformat(),
                "order_id": f"order-acq-{email}",
                "gross_amount": gross,
                "discounts": 0.0,
                "shipping_fee": 4.99,
                "payment_gateway_fee": round(gross * 0.029 + 0.30, 2),
                "cogs_total": round(gross * 0.25, 2),
                "currency": "USD",
                "attribution_utm_source": random.choice(["meta", "google", "organic"]),
                "attribution_utm_campaign": "demo-campaign",
                "customer_email": email,
            }
        )
    r = requests.post(f"{BASE_URL}/stores/{store_id}/orders", headers=headers, json=orders)
    print("orders inserted:", r.json())

    ad_spend = []
    for day_offset in range(90):
        day = now - timedelta(days=day_offset)
        for platform in ("meta", "google"):
            ad_spend.append(
                {
                    "time": day.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                    "platform": platform,
                    "campaign_id": f"{platform}-demo-campaign",
                    "campaign_name": "Demo Campaign",
                    "adset_id": "adset-1",
                    "spend": round(random.uniform(20, 80), 2),
                    "impressions": random.randint(1000, 5000),
                    "clicks": random.randint(50, 300),
                }
            )
    r = requests.post(f"{BASE_URL}/stores/{store_id}/ad-spend", headers=headers, json=ad_spend)
    print("ad spend inserted:", r.json())

    start = (now - timedelta(days=14)).isoformat()
    end = now.isoformat()
    summary = requests.get(
        f"{BASE_URL}/stores/{store_id}/metrics/summary",
        headers=headers,
        params={"start": start, "end": end},
    ).json()
    print("\nSummary (last 14 days):")
    print(summary)
    print(f"\nTry it yourself: GET {BASE_URL}/stores/{store_id}/metrics/summary?start={start}&end={end}")
    print(f'  -H "Authorization: Bearer {token}"')


if __name__ == "__main__":
    main()
