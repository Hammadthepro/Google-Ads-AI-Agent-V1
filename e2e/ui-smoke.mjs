// Browser E2E against the running stack with REAL integration behaviour. There is no demo data and no fake service here.
//
//   cd backend && python -m app.cli create-super-admin smoke-admin@example.com      # once
//   node e2e/ui-smoke.mjs [baseUrl]            (SMOKE_EMAIL overrides the sign-in email, SHOTS_DIR saves screenshots)
//
// Verifies: every page renders for a real (empty) database; the dashboard shows proper empty / "not configured" states
// instead of numbers; the Integrations page lists every integration and never puts a secret in the DOM; the operating mode is
// read-only with real writes disabled; a client can be created through the form; its audit runs against the real
// integrations that ARE configured and reports the others as unavailable instead of inventing results.
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.argv[2] || "http://localhost:5173";
const EMAIL = process.env.SMOKE_EMAIL || "smoke-admin@example.com";
const shots = process.env.SHOTS_DIR;
const errors = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
// ignored: blocked web fonts in sandboxed networks, favicon, the expected 401 before sign-in, and the expected 424 "integration not configured"
page.on("console", (m) => {
  const t = m.text();
  if (m.type() !== "error" || /favicon|ERR_TUNNEL_CONNECTION_FAILED|fonts\.g|401|424/.test(t)) return;
  errors.push(`console: ${t}`);
});

// 1. sign in with the email of an existing user (dev auth); an unknown email must be refused
await page.goto(`${BASE}/login`);
await page.getByLabel("Email").fill("nobody@example.com");
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByText(/has not been invited/).waitFor();
await page.getByLabel("Email").fill(EMAIL);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL(`${BASE}/`);

// 2. every navigation page renders
for (const path of ["/", "/clients", "/businesses", "/accounts", "/tasks", "/audits", "/planner", "/approvals", "/monitor", "/weekly",
                    "/experiments", "/learning", "/users", "/integrations", "/settings", "/activity"]) {
  await page.goto(`${BASE}${path}`);
  await page.waitForLoadState("networkidle");
  const h1 = await page.locator("h1").first().textContent();
  if (!h1) errors.push(`no heading on ${path}`);
  if (shots) await page.screenshot({ path: `${shots}/page${path.replace(/\//g, "_") || "_home"}.png` });
}

// 3. nothing simulated anywhere in the shell
const banner = (await page.getByRole("status").first().textContent()) || "";
if (!banner.includes("Real account") || !banner.includes("Real campaign writes: DISABLED")) errors.push(`operating-mode banner wrong: ${banner}`);
for (const path of ["/", "/accounts", "/monitor", "/settings", "/integrations"]) {
  await page.goto(`${BASE}${path}`);
  await page.waitForLoadState("networkidle");
  const text = await page.locator("body").innerText();
  if (/MOCK DATA|Mock mode|simulated clock|SIMULATED|Reset & reseed|demo user/i.test(text)) errors.push(`simulation wording on ${path}`);
}

// 4. dashboard empty state
await page.goto(`${BASE}/`);
await page.waitForLoadState("networkidle");
const dash = await page.locator("body").innerText();
if (!/No clients yet|Your first real campaign analysis will appear here after connecting Google Ads|No Google Ads (account is linked|data available)|Google Ads integration is not configured/.test(dash)) errors.push("dashboard empty state missing");

// 5. Integrations: every card present, no secret in the DOM, writes disabled
await page.goto(`${BASE}/integrations`);
await page.getByRole("heading", { name: "Integrations" }).waitFor();
for (const label of ["Google Ads API", "Google Analytics 4", "Google Business Profile", "Groq (internal AI)", "Claude (Anthropic)", "PageSpeed Insights"]) {
  if (!(await page.getByText(label).first().isVisible())) errors.push(`integration card missing: ${label}`);
}
if (!(await page.getByText("Real campaign writes possible right now").isVisible())) errors.push("write status missing");
if (await page.getByText("Mock mode").count()) errors.push("mock mode option still offered");
if (/gsk_|sk-ant-|ya29\.|1\/\/0/.test(await page.content())) errors.push("secret-looking value in DOM");
// every integration shows its status, required fields and (where relevant) dependencies; infrastructure is marked deployment-managed
for (const label of ["Google Analytics 4", "Google Business Profile", "Keyword Planner", "Claude (Anthropic)", "Google Places API", "Website crawler",
                     "Email alerts (SMTP)", "Google sign-in (app login)", "Database (PostgreSQL / Supabase)", "Redis (queue & rate limits)"]) {
  if (!(await page.getByText(label).first().isVisible())) errors.push(`integration card missing: ${label}`);
}
for (const section of ["Google", "Other AI", "Website research", "System"]) {
  if (!(await page.locator("h2", { hasText: new RegExp(`^${section}$`) }).count())) errors.push(`category missing: ${section}`);
}
if ((await page.getByText("Deployment-managed").count()) < 3) errors.push("deployment-managed marking missing");
if (!(await page.getByLabel("Dependencies").count())) errors.push("dependency panel missing");
if (!(await page.getByText(/SMTP host/).count())) errors.push("SMTP form fields missing");
// typing in a field flags an UNSAVED change until Save is pressed
const cid = page.getByLabel(/OAuth client ID/).first();
await cid.fill("typed-but-not-saved.apps.googleusercontent.com");
if (!(await page.getByText(/Unsaved changes — not saved on the server yet/).count())) errors.push("unsaved-change indicator missing");
await page.getByRole("button", { name: "Discard" }).first().click();
if (shots) await page.screenshot({ path: `${shots}/integrations.png`, fullPage: true });

// 6. create a client through the form, then watch its real audit
{
  await page.goto(`${BASE}/clients/new`);
  await page.getByText(/field\(s\) to fix/).waitFor();
  await page.getByLabel("Business name").fill("Smoke Test Plumbing");
  await page.getByLabel("Business category").fill("plumber");
  await page.getByLabel("Business phone").fill("5125550100");
  await page.getByLabel("Website *").fill(`smoke-${Date.now()}.example.com`);
  await page.getByRole("button", { name: "Yes", exact: true }).click();
  for (const [label, value] of [["Services input", "Drain Cleaning"], ["Service areas: cities or zip codes input", "Austin, TX 78701"]]) {
    const el = page.getByLabel(label); await el.fill(value); await el.press("Enter");
  }
  await page.getByLabel("Monthly ad budget (USD)").fill("1.5k");
  await page.getByText("Ready to save").waitFor();
  await page.getByRole("button", { name: "Save client" }).click();
  await page.waitForURL(/\/clients\/[0-9a-f]+\?created=1/);
  const created = await page.getByText(/Client created/).textContent();
  if (!/CL-\d{6}/.test(created)) errors.push("client ID missing after create");
  await page.getByRole("button", { name: "Watch the analysis live" }).click();
  await page.waitForURL(/audit-reports\//);
  await page.getByText("Overall client readiness").waitFor({ timeout: 120000 });
  const report = await page.locator("body").innerText();
  const hit = report.match(/.{50}(SIMULATED|simulated data|MOCK).{50}/is); if (hit) errors.push("simulated wording in audit report: " + hit[0].replace(/\s+/g, " "));
  if (shots) await page.screenshot({ path: `${shots}/audit_report.png`, fullPage: true });
  // the dashboard lists the client but must not invent numbers for it
  await page.goto(`${BASE}/`);
  await page.waitForLoadState("networkidle");
  const row = page.locator("tr", { hasText: "Smoke Test Plumbing" }).first();
  const cells = (await row.innerText()).replace(/\s+/g, " ");
  if (!/Not configured|No account linked|Connected but no data/.test(cells)) errors.push(`client row lacks a data status: ${cells}`);
  if (/\$\d/.test(cells)) errors.push(`client row shows money without data: ${cells}`);
}

// 7. account selector: honest state with the real (Test-access) Google connection — no fabricated accounts
await page.goto(`${BASE}/accounts`);
await page.waitForLoadState("networkidle");
await page.getByText("Client accounts", { exact: true }).first().waitFor();
await page.getByLabel("Search accounts").fill("zzz-no-such-account");
await page.getByText(/No accounts match|No client accounts yet/).first().waitFor();
const acctText = await page.locator("body").innerText();
if (!/No manager \(MCC\) account is selected yet|never refreshed|No client accounts yet|Google Ads integration is not configured/.test(acctText)) errors.push("account selector shows neither an empty state nor a manager prompt");
if (/MOCK|SIMULATED/i.test(acctText)) errors.push("simulation wording on accounts page");
await page.goto(`${BASE}/integrations`);
await page.waitForLoadState("networkidle");
if (await page.getByText(/GOOGLE_ADS_CUSTOMER_ID|Client \(test\) account customer ID/).count()) errors.push("retired client-ID field still shown");
if (shots) await page.screenshot({ path: `${shots}/accounts.png`, fullPage: true });

await browser.close();
if (errors.length) { console.error("UI SMOKE FAILED:\n" + errors.join("\n")); process.exit(1); }
console.log("UI SMOKE PASSED: pages render on an empty database; honest empty/not-configured states; no simulation wording; no secrets in the DOM; client + real audit flow works");
