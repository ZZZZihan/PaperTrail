/** Deterministic real-browser assertions. Launched only by check_browser.py. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "../web/node_modules/playwright/index.mjs";

const config = JSON.parse(readFileSync(0, "utf8"));
const evidence = { status: "RUNNING", checks: [], requests: [], external_requests: [], page_errors: [] };
const origin = new URL(config.base_url).origin;
assert.equal(new URL(origin).hostname, "127.0.0.1");
let browser;

function passed(name, details = {}) {
  evidence.checks.push({ name, status: "PASS", ...details });
  console.log(`BROWSER PASS: ${name}`);
}

try {
  browser = await chromium.launch({ headless: true });
  evidence.browser_version = browser.version();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const health = await context.request.get(`${origin}/health`);
  assert.equal(health.headers()["x-papertrail-fixture"], config.fixture_model);
  await context.route("**/*", async (route) => {
    const request = route.request();
    if (new URL(request.url()).origin !== origin) {
      evidence.external_requests.push(request.url());
      return route.abort("blockedbyclient");
    }
    if (!["GET", "HEAD"].includes(request.method())) {
      const marker = await context.request.get(`${origin}/health`);
      if (marker.headers()["x-papertrail-fixture"] !== config.fixture_model)
        return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => evidence.page_errors.push(error.message));
  page.on("request", (request) => evidence.requests.push({ method: request.method(), url: request.url() }));
  const postCount = () => evidence.requests.filter((request) => request.method === "POST").length;
  await page.goto(origin);
  const uploadResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/papers");
  await page.getByLabel("选择 PDF 文件").setInputFiles(config.pdf);
  const imported = await (await uploadResponse).json();
  const paperId = imported.paper.id;
  await page.locator("#questions-tab").click();
  passed("Upload a synthetic PDF through the isolated browser UI", { paper_id: paperId });

  async function submit(question, status) {
    await page.getByLabel("从你想理解的问题开始").fill(question);
    const responsePromise = page.waitForResponse((response) =>
      response.request().method() === "POST" && response.url().endsWith(`/papers/${paperId}/questions`));
    await page.getByRole("button", { name: "查找证据并回答 →", exact: true }).click();
    const response = await responsePromise;
    assert.equal(response.status(), 202);
    const submitted = await response.json();
    const card = page.locator(`.question-card.question-${status}`).filter({ hasText: question });
    await card.waitFor({ state: "visible" });
    const stored = await (await context.request.get(
      `${origin}/api/papers/${paperId}/questions/${submitted.id}`,
    )).json();
    assert.equal(stored.status, status);
    return { card, stored };
  }

  function gpuCard() {
    return page.locator(".question-card.question-insufficient_evidence")
      .filter({ hasText: config.gpu_question });
  }

  async function assertGpuPresentation(card) {
    const requested = card.locator(".answer-coverage");
    const background = card.getByRole("region", { name: "补充背景", exact: true });
    const conclusion = card.locator(".answer-insufficient-conclusion");
    assert.equal(await requested.locator("li").count(), 3);
    for (const requirement of config.gpu_requirements) {
      const item = requested.locator("li").filter({ hasText: requirement });
      assert.equal(await item.count(), 1);
      assert.match(await item.innerText(), /^当前证据无法提供：/);
      assert.ok((await conclusion.innerText()).includes(requirement));
    }
    assert.equal(await background.locator(".answer-claim").count(), 2);
    assert.equal(await card.locator(".question-body > .answer-claim").count(), 0);
    assert.equal(await background.locator(".background-coverage li").count(), 3);
    assert.ok(!(await card.innerText()).includes("已回答"));
    assert.match(await conclusion.innerText(), /证据不足。$/);
    assert.ok(await requested.evaluate((element) =>
      !!(element.compareDocumentPosition(element.parentElement.querySelector(".answer-background"))
        & Node.DOCUMENT_POSITION_FOLLOWING)));
    assert.ok(await background.evaluate((element) =>
      !!(element.compareDocumentPosition(element.parentElement.querySelector(".answer-insufficient"))
        & Node.DOCUMENT_POSITION_FOLLOWING)));
  }

  const { card, stored } = await submit(config.gpu_question, "insufficient_evidence");
  assert.equal(stored.coverage.status, "unanswered");
  assert.ok(stored.claims.every((claim) => claim.role === "background"));
  assert.equal(stored.trace.call_count, 3);
  assert.ok(stored.trace.calls.every((call) => call.model === config.fixture_model));
  await assertGpuPresentation(card);
  evidence.gpu_result = stored;
  passed("Exact GPU model/count/electricity request lists each missing item before secondary background and ends insufficient");
  await page.screenshot({ path: join(config.output_dir, "gpu-insufficient-desktop.png"), fullPage: true });
  await card.locator(".answer-insufficient-conclusion").scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(config.output_dir, "gpu-insufficient-desktop-conclusion.png"), fullPage: true });

  const postsBeforeRead = postCount();
  const citationResults = [];
  const citations = stored.claims.flatMap((claim) => claim.citations);
  for (const [index, citation] of citations.entries()) {
    await card.locator(".answer-background .citation-link").nth(index).click();
    await page.getByRole("status").filter({ hasText: `已定位引用 · PDF 第 ${citation.page_index + 1} 页` }).waitFor();
    assert.equal(await page.getByLabel("PDF 页码", { exact: true }).inputValue(), String(citation.page_index + 1));
    await page.locator(`canvas[aria-label="原 PDF 第 ${citation.page_index + 1} 页"]`).waitFor({ state: "visible" });
    await page.getByRole("button", { name: "在提取文字中查看引用", exact: true }).click();
    const highlight = page.locator(".page-text mark");
    await highlight.waitFor({ state: "visible" });
    assert.equal(await highlight.innerText(), citation.quote);
    citationResults.push({ page_index: citation.page_index, quote: citation.quote, highlighted: true });
  }
  passed("Every retained background citation opens its original PDF page and exact text highlight", { citations: citationResults });

  await page.reload();
  await page.locator("#questions-tab").click();
  await gpuCard().locator(".answer-insufficient-conclusion").waitFor();
  await assertGpuPresentation(gpuCard());
  const refreshedHistory = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/papers/${paperId}/questions`));
  await page.getByRole("button", { name: "刷新历史", exact: true }).click();
  await refreshedHistory;
  const reloaded = await (await context.request.get(`${origin}/api/papers/${paperId}/questions/${stored.id}`)).json();
  assert.deepEqual(reloaded, stored);
  assert.equal(postCount(), postsBeforeRead);
  passed("Citation reads, browser reload, and history refresh preserve the exact result with zero POSTs", { extra_posts: 0 });

  await page.setViewportSize({ width: 390, height: 844 });
  await assertGpuPresentation(gpuCard());
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: join(config.output_dir, "gpu-insufficient-mobile.png"), fullPage: true });
  passed("Mobile view keeps requested deficits and final insufficiency visible without horizontal overflow");
  await page.setViewportSize({ width: 1440, height: 1000 });

  const partial = await submit("部分回答：Omega 位于哪一页？", "partial_answer");
  assert.equal(partial.stored.coverage.status, "partial");
  assert.ok(partial.stored.claims.some((claim) => claim.role === "answer"));
  assert.ok((await partial.card.innerText()).includes("已回答"));
  assert.equal(await partial.card.locator(".question-body > .answer-claim").count(), 1);
  assert.equal(await partial.card.locator(".answer-insufficient-conclusion").count(), 0);
  assert.ok((await partial.card.locator(".answer-coverage").innerText()).includes("试验轮数是多少？"));
  evidence.partial_result = partial.stored;
  passed("A supported requested item still produces a cited partial answer with its remaining gap");
  assert.deepEqual(evidence.external_requests, []);
  assert.deepEqual(evidence.page_errors, []);
  const history = await (await context.request.get(`${origin}/api/papers/${paperId}/questions`)).json();
  assert.equal(history.length, 2);
  assert.equal(history.reduce((sum, row) => sum + row.trace.call_count, 0), 6);
  evidence.offline_calls = 6;
  passed("Only the disposable local origin was contacted; two tasks used six mock calls and no browser errors");
  evidence.status = "PASS";
} catch (error) {
  evidence.status = "FAIL";
  evidence.error = error.stack || String(error);
  console.error(evidence.error);
  process.exitCode = 1;
} finally {
  await browser?.close();
  writeFileSync(config.result_path, JSON.stringify(evidence, null, 2) + "\n");
}
