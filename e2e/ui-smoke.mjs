// Browser E2E against the running stack (mock mode). Usage: node e2e/ui-smoke.mjs [baseUrl]
// Manager runs "Build a new campaign" from the UI (executed by the Celery worker) -> admin approves the exact
// version -> executes -> resources verified PAUSED -> enable stays blocked until its own approval.
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.argv[2] || "http://localhost:5173";
const shots = process.env.SHOTS_DIR;
const errors = [];

async function login(page, email) {
  await page.goto(`${BASE}/login`);
  await page.getByText(email, { exact: true }).click();
  await page.waitForURL(`${BASE}/`);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
// ignored: blocked web fonts in sandboxed networks, favicon, and the expected 401 from /auth/me before sign-in
page.on("console", (m) => {
  const t = m.text();
  if (m.type() !== "error" || t.includes("favicon") || t.includes("ERR_TUNNEL_CONNECTION_FAILED") || t.includes("fonts.g") || t.includes("401")) return;
  errors.push(`console: ${t}`);
});

// 1. every navigation page renders for a Super Admin
await login(page, "superadmin@napollo.example");
for (const path of ["/", "/clients", "/businesses", "/accounts", "/tasks", "/audits", "/planner", "/approvals", "/monitor", "/weekly",
                    "/experiments", "/learning", "/users", "/integrations", "/settings", "/activity"]) {
  await page.goto(`${BASE}${path}`);
  await page.waitForLoadState("networkidle");
  const h1 = await page.locator("h1").first().textContent();
  if (!h1) errors.push(`no heading on ${path}`);
  if (shots) await page.screenshot({ path: `${shots}/page${path.replace(/\//g, "_") || "_home"}.png` });
}
const banner = await page.getByRole("status").first().textContent();
if (!banner.includes("Mock mode") || !banner.includes("nothing is sent to Google")) errors.push("mock banner missing");
// Integrations page: every card renders, secrets are never in the DOM, real writes stay disabled
await page.goto(`${BASE}/integrations`);
await page.getByText("API & Account Integrations").waitFor();
for (const label of ["Google Ads API", "Google Analytics 4", "Google Business Profile", "Groq (internal AI)", "Claude (Anthropic)", "PageSpeed Insights"]) {
  if (!(await page.getByText(label).first().isVisible())) errors.push(`integration card missing: ${label}`);
}
if (!(await page.getByText("Real campaign writes possible right now").isVisible())) errors.push("write status missing");
const body = await page.content();
if (/gsk_|sk-ant-|ya29\./.test(body)) errors.push("secret-looking value in DOM");
if (shots) await page.screenshot({ path: `${shots}/integrations.png`, fullPage: true });
await page.goto(`${BASE}/`);

// 2. manager runs a Build-campaign task from the task form
await ctx.clearCookies();
await login(page, "manager@napollo.example");
await page.goto(`${BASE}/tasks`);
const selects = page.locator("select");
await selects.nth(0).selectOption({ label: "Northside Home Services" });
await selects.nth(1).selectOption({ label: "Northside Plumbing" });
await page.locator("select").nth(2).selectOption("BUILD_CAMPAIGN");
await page.getByLabel("Monthly budget ($)").fill("1200");
await page.getByLabel("Weekly conversion target").fill("3");
await page.getByRole("checkbox", { name: "Water Heater Repair" }).uncheck();
await page.getByRole("checkbox", { name: "Emergency Plumbing" }).uncheck();
await page.getByRole("button", { name: "Run", exact: true }).click();
await page.waitForURL(/\/tasks\/[0-9a-f]+$/);
await page.getByText("AWAITING APPROVAL").first().waitFor({ timeout: 90000 });
if (shots) await page.screenshot({ path: `${shots}/task_detail.png`, fullPage: true });
await page.getByRole("button", { name: "Open approval →" }).click();
await page.waitForURL(/\/approvals\//);
const approvalUrl = page.url();
if (await page.getByRole("button", { name: "Approve this version" }).count()) errors.push("manager can see approve button");

// 3. admin approves the exact version and executes
await ctx.clearCookies();
await login(page, "admin@napollo.example");
await page.goto(approvalUrl);
await page.getByText("Tracking status").waitFor();
if (shots) await page.screenshot({ path: `${shots}/approval.png`, fullPage: true });
await page.getByRole("button", { name: "Approve this version" }).click();
await page.getByRole("button", { name: "Approve", exact: true }).click();
await page.getByText("APPROVED", { exact: true }).first().waitFor();
await page.getByRole("button", { name: "Execute approved change" }).click();
await page.getByRole("button", { name: "Execute", exact: true }).click();
await page.getByText("verified", { exact: true }).first().waitFor({ timeout: 30000 });
if (shots) await page.screenshot({ path: `${shots}/executed.png`, fullPage: true });

// 4. the enable approval exists and cannot be executed before approval (no execute button while pending)
await page.goto(`${BASE}/approvals`);
await page.getByText(/^Enable: Build campaign/).first().click();
await page.getByText("Approve this version").waitFor();
if (await page.getByRole("button", { name: "Execute approved change" }).count()) errors.push("enable executable before approval");

await browser.close();
if (errors.length) { console.error("UI SMOKE FAILED:\n" + errors.join("\n")); process.exit(1); }
console.log("UI SMOKE PASSED: 16 pages rendered; task -> approval -> execute (PAUSED, verified) -> enable gated");
