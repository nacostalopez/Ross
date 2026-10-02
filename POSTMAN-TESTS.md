# Ross API - Postman Test Suite

Complete API endpoint testing suite for Ross using Postman. Tests cover authentication, stores, orders, ad spend, metrics, security scoping, alerts, account members and invites, billing, and the Mercado Pago / Mercado Libre connectors (66 requests, 123 assertions).

## Quick Start

### 1. Import into Postman

**Option A: Manual Import**
- Open Postman
- Click "Import" (top left)
- Select both files:
  - `Ross-API-Tests.postman_collection.json`
  - `Ross-Env-Local.postman_environment.json`
- Collection appears in left sidebar

**Option B: CLI Import**
```bash
# Install Newman (Postman CLI runner)
npm install -g newman

# Run full test suite
newman run Ross-API-Tests.postman_collection.json \
  -e Ross-Env-Local.postman_environment.json \
  --reporters cli,json \
  --reporter-json-export test-results.json
```

### 2. Configure Environment

In Postman:
1. Click "Environment" dropdown (top right)
2. Select "Ross - Local Dev"
3. Edit values:
   - `base_url`: `http://localhost:8100` (your API URL)
   - `login_email`: Your test email
   - `login_password`: Your test password

### 3. Run Tests

**In Postman UI:**
1. Click "Runner" button (or Cmd+Shift+E)
2. Select collection: "Ross API - Endpoint Tests"
3. Select environment: "Ross - Local Dev"
4. Click "Run" button
5. Watch tests execute and get results

**Via CLI (Newman):**
```bash
# Run all tests
newman run Ross-API-Tests.postman_collection.json \
  -e Ross-Env-Local.postman_environment.json

# Run specific folder (e.g., AUTH tests only)
newman run Ross-API-Tests.postman_collection.json \
  -e Ross-Env-Local.postman_environment.json \
  --folder AUTH

# Export results as HTML
newman run Ross-API-Tests.postman_collection.json \
  -e Ross-Env-Local.postman_environment.json \
  --reporters cli,html \
  --reporter-html-export test-results.html
```

## Test Structure

### 📁 AUTH (4 tests)
- ✅ Register Account → creates account + returns JWT
- ✅ Login → validates credentials + returns JWT
- ✅ Get Current User → verifies auth token
- ✅ Login Invalid Credentials → 401 error handling

### 📁 STORES (3 tests)
- ✅ List Stores → returns user's stores
- ✅ Create Store → creates new store with platform
- ✅ Get Store → retrieves store details

### 📁 ORDERS (2 tests)
- ✅ Ingest Orders → POST batch orders → 201
- ✅ List Orders → GET with date range filters

### 📁 AD SPEND (2 tests)
- ✅ Ingest Ad Spend → POST Meta/Google spend → 201
- ✅ List Ad Spend → GET with platform filter

### 📁 METRICS (2 tests)
- ✅ Get Summary Metrics → Revenue, Net Profit, True ROAS
  - Validates: `true_roas = net_profit / ad_spend`
  - Validates: `real_profit_after_ads = net_profit - ad_spend`
- ✅ Get Daily Metrics → Daily breakdown from continuous aggregate

### 📁 CONNECTORS (4 tests)
- ✅ Health Check → connector status + token expiry
- ✅ Shopify OAuth URL → returns authorization endpoint
- ✅ Meta OAuth URL → returns Facebook authorization
- ✅ Google OAuth URL → returns Google authorization

### 📁 SECURITY TESTS (3 tests)
- ✅ Ownership Scoping → 404 for stores user doesn't own
- ✅ Missing Token → 403 without Authorization header
- ✅ Invalid Token → 401 with bad JWT

### 📁 WEBHOOK SIMULATION (1 test)
- ✅ Shopify orders/create → acknowledges webhook

### 📁 ALERTS (5 tests)
- ✅ Defaults → alerts off, ROAS threshold 1, 3-day window
- ✅ Invalid window (15 days) → 422
- ✅ Save preferences → echoed back, then persisted
- ✅ Check now → CAC alerts list + ROAS flag

### 📁 ACCOUNT & MEMBERS (26 tests)
- ✅ Account and member list (fresh account has only its owner)
- ✅ Invite → duplicate pending invite 409 → listed without token → accept (logs the member in) → reused token 400
- ✅ Viewer permissions: can list stores, cannot ingest orders, list members or see invoices (403)
- ✅ Per-store override: admin override lets the viewer save alert preferences; clearing it blocks them again
- ✅ Role change, last-owner guard (409), cannot remove yourself (400)
- ✅ Revoke invite (204), resend a revoked invite (409), remove member (204), removed member's token rejected (401)
- ✅ Activity feed records `invite_sent`

### 📁 BILLING (7 tests)
- ✅ Plans (starter, growth, scale) with limits and `purchasable` matching `monthly_price`
- ✅ New accounts start on an active Scale subscription, with no invoices
- ✅ Checkout: unknown plan 404, plan without price 409
- ✅ Cancel without a paid subscription → 409
- ✅ Billing webhook without signature → 401

### 📁 MERCADO PAGO (3 tests)
- ✅ OAuth URL points at auth.mercadopago.com with our state
- ✅ Sync payments on a store that isn't connected → 400
- ✅ Notification without signature → 401

### 📁 MERCADO LIBRE (4 tests)
- ✅ OAuth URL points at auth.mercadolibre.com.ar with our state
- ✅ Sync orders on a store that isn't connected → 400
- ✅ Notification from an unknown application → 401; invalid JSON → 400

These run without any Mercado Pago / Mercado Libre credentials or webhook
secrets configured (as in CI), so they cover the rejection paths; the
signed-webhook and OAuth-callback happy paths are covered by the backend
pytest suite.

---

## Test Flow

**Recommended Test Order:**

1. **AUTH** - Register + Login (establishes token)
2. **STORES** - Create store (generates store_id)
3. **ORDERS** - Ingest orders (populates data)
4. **AD SPEND** - Ingest ad spend (populates ad data)
5. **METRICS** - Calculate ROAS (validates calculations)
6. **CONNECTORS** - Test OAuth flows
7. **SECURITY** - Verify ownership scoping
8. **ALERTS** - Alert preferences on the store
9. **ACCOUNT & MEMBERS** - Invite flow and permissions (uses `member_token`)
10. **BILLING**, **MERCADO PAGO**, **MERCADO LIBRE** - Plans and connector edge cases

**Each test depends on previous variables:**
```
Register → auth_token
Create Store → store_id
Ingest Orders → uses store_id
Ingest Ad Spend → uses store_id
Get Metrics → uses store_id + validates calculations
```

---

## Key Assertions

Every request includes automatic tests:

### Auth Tests
```javascript
✅ Status code is 201/200/401
✅ Response has access_token
✅ Token is valid JWT string
✅ Email matches
```

### Ownership Tests
```javascript
✅ 404 when accessing other account's store
✅ Error message is generic (no data leak)
✅ Missing token returns 403
✅ Invalid token returns 401
```

### Metrics Tests
```javascript
✅ true_roas = net_profit / ad_spend (calculation verified)
✅ real_profit_after_ads = net_profit - ad_spend
✅ All values non-negative
✅ Schema matches expected fields
```

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `base_url` | `http://localhost:8100` | API endpoint |
| `auth_token` | (empty) | JWT token (set by Register/Login) |
| `store_id` | (empty) | Store UUID (set by Create Store) |
| `login_email` | `test@example.com` | Test user email |
| `login_password` | `SecurePassword123!` | Test user password |
| `range_start` | Last 30 days | Date range for queries |
| `range_end` | Today | Date range for queries |

---

## Troubleshooting

### `404 Store not found`
- Make sure you ran "Create Store" first
- Check `store_id` variable is set
- Verify token hasn't expired

### `401 Unauthorized`
- Run "Register Account" or "Login" to get new token
- Check `auth_token` variable is populated
- Verify token format is `Bearer {token}`

### `403 Forbidden`
- Missing Authorization header
- Check "Auth" tab is set to "Bearer Token" with {{auth_token}}

### Tests fail in order
- Run tests in sequence (not random)
- Each test depends on previous variables
- Use Postman "Runner" mode with sequential execution

### Webhook tests fail
- HMAC signature is placeholder (integration test only)
- In production, calculate: HMAC-SHA256(body, api_secret)
- See [DEVELOPMENT.md](../DEVELOPMENT.md) for Shopify webhook validation

---

## Export Results

### JSON Report
```bash
newman run Ross-API-Tests.postman_collection.json \
  -e Ross-Env-Local.postman_environment.json \
  --reporters json \
  --reporter-json-export results.json
```

### HTML Report
```bash
newman run Ross-API-Tests.postman_collection.json \
  -e Ross-Env-Local.postman_environment.json \
  --reporters html \
  --reporter-html-export results.html
```

### CI/CD Integration
```yaml
# GitHub Actions example
- name: API Tests
  run: |
    npm install -g newman
    newman run Ross-API-Tests.postman_collection.json \
      -e Ross-Env-Local.postman_environment.json \
      --reporters json
      --reporter-json-export results.json
    
- name: Upload Results
  uses: actions/upload-artifact@v3
  with:
    name: postman-results
    path: results.json
```

---

## Next Steps

After Postman tests pass:

1. **Pytest Integration Tests** → More comprehensive
2. **Contract Testing** → API versioning
3. **Load Testing** → Performance under stress
4. **Webhook Simulation** → Real payload testing

See [DEVELOPMENT.md](../DEVELOPMENT.md) for Phase 1 testing roadmap.
