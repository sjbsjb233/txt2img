// Test plan §6.10 — form submission flow.
// Important: this catches the regression bugs the schema renderer
// introduced (n_max → n value_key indirection, stale-param scrubbing).

import { seedTest as test, expect } from "../fixtures/seed.js";
import { CreatePage } from "../pages/CreatePage.js";

async function captureCreateJobBody(page, action) {
  const [request] = await Promise.all([
    page.waitForRequest(
      (r) => r.method() === "POST" && r.url().includes("/api/jobs") && !r.url().includes("/precheck"),
      { timeout: 15_000 }
    ),
    action(),
  ]);
  // Multipart form-data — extract the JSON `payload` part by hand.
  const raw = request.postData() || "";
  const m = raw.match(/name="payload"[\s\S]*?\r?\n\r?\n([\s\S]*?)\r?\n--/);
  if (!m) throw new Error(`could not find payload part in:\n${raw.slice(0, 500)}`);
  return JSON.parse(m[1]);
}

test.describe("Create page · form submission", () => {

  test("clicked params reach POST /api/jobs", async ({
    premiumUserPage, bltcyRestricted, aliyunFull,
  }) => {
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    await cp.fillPrompt("a watermelon");
    await cp.setOutputCount(2);
    await cp.clickOption("size", "1536x1024");

    const body = await captureCreateJobBody(premiumUserPage, () => cp.clickSubmit());
    expect(body.prompt).toBe("a watermelon");
    expect(body.n).toBe(2);
    expect(body.size).toBe("1536x1024");
    expect(body.model).toBe("gpt-image-2");
  });

  test("REGRESSION — n_max never appears in the payload (value_key indirection)", async ({
    premiumUserPage, aliyunFull,
  }) => {
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    await cp.fillPrompt("test");
    await cp.setOutputCount(4);
    const body = await captureCreateJobBody(premiumUserPage, () => cp.clickSubmit());
    expect(body.n).toBe(4);
    expect(body.n_max).toBeUndefined();
  });

  test("REGRESSION — switching models scrubs stale params", async ({
    premiumUserPage, aliyunFull, geminiFlashFull,
  }) => {
    /** Pick gpt-image-2 quality=high, then switch to gemini and submit.
     * gemini has no `quality` field; the schema-driven reconcile must
     * drop `quality` from the request body. */
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    await cp.fillPrompt("test");
    await cp.expandAdvanced();
    await cp.clickOption("quality", "high");

    await cp.selectModel("gemini-3.1-flash-image-preview");
    const body = await captureCreateJobBody(premiumUserPage, () => cp.clickSubmit());
    expect(body.quality).toBeUndefined();
    expect(body.size).toBeUndefined();
  });

  test("Generate button disabled when no provider available", async ({
    freeUserPage,  // no provider fixture
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    expect(await cp.submitButtonDisabled()).toBe(true);
  });

  test("special chars in prompt round-trip without JSON corruption", async ({
    premiumUserPage, aliyunFull,
  }) => {
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    const tricky = `"hello"\\n<script>alert(1)</script> 你好 🍉`;
    await cp.fillPrompt(tricky);
    const body = await captureCreateJobBody(premiumUserPage, () => cp.clickSubmit());
    expect(body.prompt).toBe(tricky);
  });
});
