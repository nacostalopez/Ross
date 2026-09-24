# 🚀 Postman Tests - Quick Start

## Installation (5 min)

### Step 1: Install Postman & Newman
```bash
# Download Postman: https://www.postman.com/downloads/
# Or install Newman CLI:
npm install -g newman
```

### Step 2: Import Collections
Open Postman and import these files:
- `Ross-API-Tests.postman_collection.json` (test requests)
- `Ross-Env-Local.postman_environment.json` (variables)

### Step 3: Start API
```bash
cd backend
docker-compose up -d
# Wait for: "db: healthy"
uvicorn app.main:app --reload
# Should see: "Uvicorn running on http://127.0.0.1:8100"
```

---

## Run Tests (Choose One)

### Option A: Postman UI (Most Visual)
1. Open Postman
2. Click **Collection Runner** (top left)
3. Select: **"Ross API - Endpoint Tests"**
4. Select: **"Ross - Local Dev"**
5. Click **"Run"**
6. Watch tests execute ✅

### Option B: Command Line (Fastest)
```bash
# Windows PowerShell
.\run-postman-tests.ps1

# Mac/Linux
./run-postman-tests.sh

# Or direct Newman
newman run Ross-API-Tests.postman_collection.json \
  -e Ross-Env-Local.postman_environment.json
```

### Option C: Generate HTML Report
```bash
# Windows
.\run-postman-tests.ps1 -Format html

# Mac/Linux
./run-postman-tests.sh html
```

---

## What Gets Tested

✅ **Authentication** (4 tests)
- Register, Login, Auth checks

✅ **Data Management** (7 tests)
- Stores, Orders, Ad Spend, Metrics

✅ **Security** (3 tests)
- Ownership scoping, token validation

✅ **Integrations** (4 tests)
- Shopify/Meta/Google OAuth URLs

✅ **Webhooks** (1 test)
- Shopify webhook simulation

**Total: 19 tests**

---

## Expected Flow

```
1. Register Account
   ↓ Gets auth_token
2. Create Store
   ↓ Gets store_id
3. Ingest Orders
   ↓ Populates order data
4. Ingest Ad Spend
   ↓ Populates ad spend data
5. Get Metrics
   ↓ Validates True ROAS calculation
6. Test Security
   ↓ Ensures ownership scoping works
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| API connection refused | Check `docker-compose up` and uvicorn running |
| 401 Unauthorized | Run "Register Account" or "Login" first |
| 404 Store not found | Run "Create Store" and check store_id |
| Tests fail in sequence | Run with Runner (not individual) |
| Newman not found | `npm install -g newman` |

---

## View Results

After running:
- **CLI output**: Visible in terminal immediately
- **HTML report**: Check `test-results/` folder
- **JSON report**: Parse for CI/CD integration

---

## Next Steps

✅ **Now**: Run tests in Postman
⏳ **Then**: Move to pytest for integration tests
⏳ **Later**: Add to CI/CD pipeline

See [POSTMAN-TESTS.md](POSTMAN-TESTS.md) for detailed guide.
