# Yields — Official U.S. Interest Rates Dashboard

**Yields** is a zero-dependency, static financial dashboard that downloads, validates, and displays daily published U.S. interest rates from official government and Federal Reserve sources:

- **U.S. Department of the Treasury — Daily Par Yield Curve Rates**: All 14 published maturities (`1M`, `6W` / `BC_1_5MONTH`, `2M`, `3M`, `4M`, `6M`, `1Y`, `2Y`, `3Y`, `5Y`, `7Y`, `10Y`, `20Y`, and `30Y`), explicitly excluding the duplicate `BC_30YEARDISPLAY` field.
- **Federal Reserve Bank of New York — Effective Federal Funds Rate (EFFR)** and the **FOMC Federal Funds Target Range**.
- **Federal Reserve Bank of New York — Secured Overnight Financing Rate (SOFR)**.

All frontend assets (`site/index.html`, `site/styles.css`, `site/app.js`, and `site/data/rates.json`) use relative URLs so the site works seamlessly both at `https://YOUR-USERNAME.github.io/REPOSITORY-NAME/` and at a custom subdomain such as `https://yields.mydomain.com/`.

---

## Repository Structure

```text
.
├── .github/
│   └── workflows/
│       ├── ci.yml          # Pull-request test workflow (no commits or deployments)
│       └── publish.yml     # Production updater + GitHub Pages deployment workflow
├── scripts/
│   └── update_data.py      # Python 3 standard-library data downloader & normalizer
├── site/
│   ├── data/
│   │   └── rates.json      # Normalized historical & latest official observations
│   ├── app.js              # Vanilla JS dashboard, SVG charts, tables, CSV exporter
│   ├── index.html          # Accessible responsive dashboard markup
│   └── styles.css          # Light & dark theme financial dashboard styles
├── tests/
│   └── test_update_data.py # Unit tests for XML/JSON parsing, revisions, spreads & errors
├── .gitignore
├── LICENSE                 # MIT License + official data-provider attribution & terms
└── README.md
```

---

## 1. Running the Updater and Website Locally

### Prerequisites
- **Python 3.9+** (uses only standard-library modules; no `pip install` needed).
- Any modern web browser.

### Step 1: Run the unit tests
From the repository root:

```bash
python3 -m unittest discover -s tests -v
```

### Step 2: Download and update official rate data
Run the updater script:

```bash
python3 scripts/update_data.py
```

- **First run**: Downloads the current calendar year and the previous two calendar years of official Treasury XML, EFFR JSON, and SOFR JSON history and writes `site/data/rates.json`.
- **Subsequent runs**: Fetches recent months/observations, merges revisions, deduplicates by date, preserves missing values as `null` (never `0`), and only rewrites `site/data/rates.json` when meaningful observations or source statuses change.

### Step 3: Serve the website locally over HTTP
Because browsers block `fetch("./data/rates.json")` when opening HTML files via `file://`, serve the project with Python’s built-in HTTP server:

```bash
# Serve the site/ directory directly at http://127.0.0.1:43123/
python3 -m http.server 43123 --bind 127.0.0.1 --directory site
```

Or serve from the repository root to verify subpath hosting (simulating `username.github.io/repo-name/`):

```bash
python3 -m http.server 43123 --bind 127.0.0.1
# Then open http://127.0.0.1:43123/site/
```

---

## 2. Creating a Public GitHub Repository and Uploading the Project

1. Sign in to [GitHub](https://github.com/) and click the **+** icon in the top-right corner → **New repository**.
2. Set **Repository name** (for example, `yields`).
3. Select **Public** (GitHub Pages is available for free on public repositories).
4. Leave “Add a README file”, “Add .gitignore”, and “Choose a license” **unchecked** because this project already includes them.
5. Click **Create repository**.
6. Push or upload the **entire project**, making sure hidden dot-folders—especially `.github/workflows/publish.yml`, `.github/workflows/ci.yml`, and `.gitignore`—are included:

```bash
git init -b main
git add .
git commit -m "Initial commit: Yields official U.S. interest rates dashboard"
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
git push -u origin main
```

> **Important if uploading via the GitHub web interface**: Dragging and dropping folders in a browser sometimes skips hidden directories starting with a dot (`.github`). Verify after uploading that `.github/workflows/publish.yml` and `.github/workflows/ci.yml` exist in your repository on GitHub.

---

## 3. Enabling GitHub Actions and Configuring GitHub Pages

1. In your GitHub repository, click **Settings** (top navigation bar).
2. In the left sidebar, click **Pages** (under “Code and automation”).
3. Under **Build and deployment** → **Source**, open the dropdown and select **GitHub Actions** (instead of “Deploy from a branch”).
4. In the left sidebar, click **Actions** → **General** and confirm that **Actions permissions** allows workflows to run.

---

## 4. Manually Running the Publishing Workflow

1. Click the **Actions** tab at the top of your GitHub repository.
2. In the left sidebar, click **Update Data and Publish to GitHub Pages**.
3. If GitHub shows a banner asking to enable workflows on this repository, click **I understand my workflows, go ahead and enable them**.
4. Click the **Run workflow** dropdown button on the right side, keep `Branch: main` selected, and click the green **Run workflow** button.
5. Wait roughly 30–60 seconds for the two jobs (`Test, Update Official Rates, and Package Site` and `Deploy to GitHub Pages`) to turn green.

---

## 5. Finding Your Default GitHub Pages URL

Once the workflow finishes:
- Look at the **Deploy to GitHub Pages** job box inside the workflow run summary; the deployed URL is printed directly underneath the job name.
- Or go to **Settings** → **Pages**, where GitHub displays:
  `Your site is live at https://YOUR-USERNAME.github.io/YOUR-REPO/`

Open that URL first and confirm the dashboard loads all cards, charts, and tables before connecting a custom domain.

---

## 6. Connecting a Custom Subdomain (`yields.mydomain.com`) via Wix DNS

> **Important DNS Note**: Having a website hosted on Wix does **not** automatically mean Wix manages your domain’s DNS. DNS records must be added at the **actual DNS provider / authoritative nameserver** for your domain (for example, Wix, Cloudflare, GoDaddy, Namecheap, or Route 53). You can check where your domain’s nameservers point in your domain registrar’s dashboard. If your domain’s nameservers point to Wix (`ns*.wixdns.net`), follow the Wix steps below.

### Step A: Verify domain ownership on GitHub (Recommended)
Verifying your domain prevents another GitHub user from claiming your subdomain if you ever unpublish your Pages site:
1. Click your GitHub profile photo (top right) → **Settings** → **Pages** (in your personal or organization account settings).
2. Click **Add a domain**, enter `mydomain.com` (or `yields.mydomain.com`), and follow GitHub’s instructions to add the `_github-pages-challenge-...` `TXT` record in your DNS provider, then click **Verify**.

### Step B: Set the custom domain in your GitHub repository
1. Open your repository on GitHub → **Settings** → **Pages**.
2. Under **Custom domain**, enter your desired subdomain, e.g. `yields.mydomain.com`, and click **Save**.
   *(GitHub will begin checking DNS; it is normal to see a DNS warning until Step C propagates.)*

### Step C: Add the `CNAME` record in Wix Domains (or your actual DNS provider)
1. Sign in to your [Wix Domains page](https://manage.wix.com/account/domains).
2. Next to your domain (`mydomain.com`), click the **More Actions** (`...`) button and select **Manage DNS Records**.
3. Scroll down to the **CNAME (Aliases)** section and click **+ Add Record**.
4. Fill in the fields carefully:
   - **Host name**: Enter only `yields` (Wix automatically appends `.mydomain.com`).
   - **Value (Points to)**: Enter `YOUR-USERNAME.github.io` (replace `YOUR-USERNAME` with your exact GitHub username; **do not** include `/repository-name` or `https://`).
   - **TTL**: Leave at the default (typically 1 Hour).
5. Click **Save** → **Save Changes**.

---

## 7. Enabling HTTPS

1. Wait 5 to 60 minutes (occasionally up to 24 hours) for DNS propagation to complete.
2. Return to your GitHub repository → **Settings** → **Pages**.
3. Once GitHub shows `DNS check successful`, check the box labeled **Enforce HTTPS**.
   *(If the checkbox is temporarily grayed out while GitHub provisions your Let’s Encrypt TLS certificate, wait 10–15 minutes and refresh the page.)*

---

## 8. Scheduled Updates & GitHub’s 60-Day Inactivity Rule

The production workflow (`.github/workflows/publish.yml`) runs automatically on Monday through Friday at **15:17 UTC** and **23:17 UTC**, in addition to pushes to `main` and manual `workflow_dispatch` runs.

- **Meaningful commits only**: When new official rates are published (or when a source status changes), the workflow commits the updated `site/data/rates.json` back to `main` and deploys the updated `site/` artifact to GitHub Pages in the **same workflow run**. If nothing changed except the check timestamp, no noisy commit is created.
- **GitHub’s scheduled-workflow inactivity rule**: On public repositories, GitHub Actions automatically disables scheduled (`cron`) workflows if **60 consecutive days** pass with no repository activity (such as commits or pushes). Because `publish.yml` commits updated data on market business days, normal daily data updates count as repository activity and keep the schedule alive. However, if all updates fail for an extended period or you pause commits for 60 days, GitHub will disable the schedule and you will need to re-enable it under the **Actions** tab.

---

## 9. Troubleshooting

- **Scheduled workflow did not run or appears disabled**:
  Open the **Actions** tab → select **Update Data and Publish to GitHub Pages**. If a yellow banner says the scheduled workflow was disabled due to inactivity, click **Enable workflow** and trigger a manual run via **Run workflow**. Note that GitHub Actions cron schedules can also experience brief queue delays of 5–15 minutes during peak hours.
- **Partial or failed data updates**:
  - If **one** official source is temporarily unreachable (for example, a transient maintenance window at `home.treasury.gov` or `markets.newyorkfed.org`), `scripts/update_data.py` retains the last valid history for that source, marks its status in `site/data/rates.json`, and updates the remaining sources. The website displays a clear banner identifying the affected source.
  - If **every** source fails, `scripts/update_data.py` exits with a non-zero status code, failing the workflow run so your previously deployed GitHub Pages website remains intact.
- **Workflow permission errors (`403 Forbidden` on push or deploy)**:
  Verify that `.github/workflows/publish.yml` is unmodified (it explicitly grants `contents: write`, `pages: write`, and `id-token: write` to the `build` job and `pages: write` / `id-token: write` to the `deploy` job) and that **Settings** → **Pages** → **Source** is set to **GitHub Actions**.
- **Custom domain shows 404 or DNS check fails**:
  1. Confirm the `CNAME` record value is `YOUR-USERNAME.github.io` (without the repository name).
  2. Verify whether Wix or another provider (such as Cloudflare or your registrar) is the authoritative DNS provider for your domain.
  3. Wait for TTL expiration and check propagation with `dig yields.mydomain.com CNAME`.

---

## 10. License and Data Attribution

Original code in this repository is licensed under the [MIT License](./LICENSE).

Interest rate series are public official data published by:
- **U.S. Department of the Treasury**: [Daily Treasury Par Yield Curve Rates](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve) ([XML Feed Docs](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed))
- **Federal Reserve Bank of New York**: [EFFR](https://www.newyorkfed.org/markets/reference-rates/effr) and [SOFR](https://www.newyorkfed.org/markets/reference-rates/sofr) ([Markets Data API Docs](https://markets.newyorkfed.org/static/docs/markets-api.html) · [Terms of Use](https://www.newyorkfed.org/markets/data-api-intangibles/terms-of-use))
