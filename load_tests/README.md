# Staging load tests

`locustfile.py` covers the normal read paths: health, OTTO catalog, seller
products and notifications, manager products, sellers, EAN pool, publications,
and marketplace payload previews. It deliberately does **not** create products,
upload/process images, send e-mail/push, import EANs, generate AI content, or
call marketplace operations. Those endpoints have external side effects and
must be measured only in isolated one-off tests.

Run only against stage, never production.

## Disabling rate limits for the duration of a test

API throttling distorts load-test numbers. The backend supports a
`LOAD_TEST_DISABLE_THROTTLING=1` environment variable that raises every
throttle rate to an effectively unlimited value (stage only, never enable
on production). Toggle it with:

```powershell
.\load_tests\throttling-stage.ps1 on      # before the tests
.\load_tests\throttling-stage.ps1 status  # verify
.\load_tests\throttling-stage.ps1 off     # after the tests (do not forget!)
```

Note: the stage image must already contain the flag support (branch with
the `LOAD_TEST_DISABLE_THROTTLING` block in `config/settings/base.py`
deployed to stage).

## Staged run with an automatic stop

`run-staged.ps1` runs the 20 → 50 → 100 → 200 user stages sequentially
with a 60s cooldown in between and stops as soon as a stage ends with an
error rate above 1%:

```powershell
.\load_tests\run-staged.ps1
```

During the run keep an eye on the server: `ssh alikhan@31.70.112.98` and
`htop` / `docker stats`. Stage and production share the same machine, so
abort if CPU or RAM stays saturated.

## Manual setup

```powershell
python -m pip install -r requirements-load.txt

# Prefer short-lived access tokens: no login-throttle noise while spawning users.
$env:LOAD_TEST_SELLER_ACCESS_TOKEN="seller_access_token"
$env:LOAD_TEST_MANAGER_ACCESS_TOKEN="manager_access_token"

# Optional but recommended: existing stage records for detail/preview endpoints.
$env:LOAD_TEST_SELLER_PRODUCT_ID="1"
$env:LOAD_TEST_MANAGER_PRODUCT_ID="1"
$env:LOAD_TEST_OTTO_GROUP_ID="3593"
$env:LOAD_TEST_MARKETPLACE_ACCOUNT="jv"

New-Item -ItemType Directory -Force load_tests/results | Out-Null
locust -f load_tests/locustfile.py --host https://stage.benim.automatonsoft.de
```

Open `http://127.0.0.1:8089`, start with 20 users and spawn rate 2/s for five
minutes. Then run these headless stages separately:

```powershell
locust -f load_tests/locustfile.py --host https://stage.benim.automatonsoft.de --headless -u 20  -r 2  -t 5m --csv load_tests/results/read-20
locust -f load_tests/locustfile.py --host https://stage.benim.automatonsoft.de --headless -u 50  -r 5  -t 10m --csv load_tests/results/read-50
locust -f load_tests/locustfile.py --host https://stage.benim.automatonsoft.de --headless -u 100 -r 10 -t 10m --csv load_tests/results/read-100
locust -f load_tests/locustfile.py --host https://stage.benim.automatonsoft.de --headless -u 200 -r 10 -t 15m --csv load_tests/results/read-200
```

The supported user count is the highest completed stage with error rate below
1%, no sustained CPU/RAM or database saturation, and acceptable p95 latency.
In Locust, sort the Statistics table (or `*_stats.csv`) by p95 and failures:
the rows at the top are the weak endpoints worth profiling next.

If access tokens are not set, Locust falls back to credentials
(`LOAD_TEST_SELLER_EMAIL` or `LOAD_TEST_SELLER_USERNAME` plus password, and
the same for manager). The login body is email + password. Do this only with a low spawn rate:
the login endpoint is intentionally rate limited.
