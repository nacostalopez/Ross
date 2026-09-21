# Escal API - Postman Test Suite

Complete API endpoint testing suite for Escal using Postman. Tests cover authentication, stores, orders, ad spend, metrics, and security scoping.

## Quick Start

### 1. Import into Postman

**Option A: Manual Import**
- Open Postman
- Click "Import" (top left)
- Select both files:
  - `Escal-API-Tests.postman_collection.json`
  - `Escal-Env-Local.postman_environment.json`
- Collection appears in left sidebar

**Option B: CLI Import**
```bash
# Install Newman (Postman CLI runner)
npm install -g newman

# Run full test suite
newman run Escal-API-Tests.postman_collection.json \
  -e Escal-Env-Local.postman_environment.json \
  --reporters cli,json \
  --reporter-json-export test-results.json
```

### 2. Configure Environment

In Postman:
1. Click "Environment" dropdown (top right)
2. Select "Escal - Local Dev"
3. Edit values:
   - `base_url`: `http://localhost:8100` (your API URL)
   - `login_email`: Your test email
   - `login_password`: Your test password

### 3. Run Tests

**In Postman UI:**
1. Click "Runner" button (or Cmd+Shift+E)
2. Select collection: "Escal API - Endpoint Tests"
3. Select environment: "Escal - Local Dev"
4. Click "Run" button
5. Watch tests execute and get results

**Via CLI (Newman):**
```bash
# Run all tests
newman run Escal-API-Tests.postman_collection.json \
  -e Escal-Env-Local.postman_environment.json

# Run specific folder (e.g., AUTH tests only)
newman run Escal-API-Tests.postman_collection.json \
  -e Escal-Env-Local.postman_environment.json \
  --folder AUTH

# Export results as HTML
newman run Escal-API-Tests.postman_collection.json \
  -e Escal-Env-Local.postman_environment.json \
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
newman run Escal-API-Tests.postman_collection.json \
  -e Escal-Env-Local.postman_environment.json \
  --reporters json \
  --reporter-json-export results.json
```

### HTML Report
```bash
newman run Escal-API-Tests.postman_collection.json \
  -e Escal-Env-Local.postman_environment.json \
  --reporters html \
  --reporter-html-export results.html
```

### CI/CD Integration
```yaml
# GitHub Actions example
- name: API Tests
  run: |
    npm install -g newman
    newman run Escal-API-Tests.postman_collection.json \
      -e Escal-Env-Local.postman_environment.json \
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
