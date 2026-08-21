# Load tests

These Locust scenarios are deliberately read-only. Run them only against a staging server with dedicated seller and manager accounts; they never call AI, image processing, or marketplace publication endpoints.

```powershell
pip install -r requirements-load.txt

$env:LOAD_TEST_USERNAME="staging_seller"
$env:LOAD_TEST_PASSWORD="replace_me"
$env:LOAD_TEST_MANAGER_USERNAME="staging_manager"
$env:LOAD_TEST_MANAGER_PASSWORD="replace_me"

locust -f load_tests/locustfile.py --host https://staging.example.com --users 50 --spawn-rate 5 --run-time 10m --headless
```

Start with 20 users for five minutes, then test 50 and 100 users. Watch p95 response time, error rate, PostgreSQL connections, Redis memory, worker queue lengths, and CPU/RAM. Do not run a load test against production without an agreed maintenance window.
