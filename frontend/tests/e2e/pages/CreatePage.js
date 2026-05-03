// Page Object for the Create page (test plan §6.2).
//
// All locators are scoped to data-test-* attributes added in
// CreatePage.jsx + SchemaParamsPanel.jsx — see Appendix A of the test
// plan for the full taxonomy.

export class CreatePage {
  constructor(page) {
    this.page = page;
  }

  async goto() {
    await this.page.goto("/create");
    // Wait for both the param panel root AND the model strip's first
    // tile — the strip is populated after the /api/models response
    // returns, so without this the next selectModel() races the fetch.
    await this.page.waitForSelector("[data-test-param-panel]");
    await this.page.waitForSelector("[data-test-model-id]", { timeout: 15_000 });
  }

  // ----- Model strip ----------------------------------------------------

  modelTile(modelId) {
    return this.page.locator(`[data-test-model-id="${modelId}"]`);
  }

  async selectModel(modelId) {
    await this.modelTile(modelId).click();
    await this.page.waitForFunction(
      (id) =>
        document
          .querySelector("[data-test-active-model]")
          ?.getAttribute("data-test-active-model") === id,
      modelId
    );
  }

  async getActiveModelId() {
    return await this.page
      .locator("[data-test-active-model]")
      .first()
      .getAttribute("data-test-active-model");
  }

  async getVisibleModels() {
    return await this.page
      .locator("[data-test-model-id]")
      .evaluateAll((els) => els.map((e) => e.dataset.testModelId));
  }

  async modelIsAvailable(modelId) {
    return (
      (await this.modelTile(modelId).getAttribute("data-test-available")) === "true"
    );
  }

  // ----- Param panel ----------------------------------------------------

  paramPanel() {
    return this.page.locator("[data-test-param-panel]");
  }

  paramField(k) {
    return this.page.locator(`[data-test-field="${k}"]`);
  }

  async fieldExists(k) {
    return (await this.paramField(k).count()) > 0;
  }

  async getFieldOptions(k) {
    return await this.paramField(k)
      .locator("[data-test-option]")
      .evaluateAll((els) =>
        els.map((e) => ({
          value: e.dataset.testOption,
          disabled: e.hasAttribute("disabled"),
          active: e.dataset.testActive === "true",
        }))
      );
  }

  async clickOption(k, value) {
    await this.paramField(k)
      .locator(`[data-test-option="${value}"]`)
      .click({ force: false });
  }

  async isFieldDisabled(k) {
    return (
      (await this.paramField(k).getAttribute("data-test-disabled")) === "true"
    );
  }

  async getDisabledReason(k) {
    return await this.paramField(k)
      .locator("[data-test-disabled-reason]")
      .first()
      .textContent()
      .catch(() => null);
  }

  // ----- Output count ---------------------------------------------------

  async getOutputCount() {
    return Number(
      await this.page.locator("[data-test-output-count]").textContent()
    );
  }

  async setOutputCount(n) {
    await this.paramField("n_max")
      .locator(`[data-test-option="${n}"]`)
      .click();
  }

  // ----- Advanced -------------------------------------------------------

  advancedSection() {
    return this.page.locator("[data-test-advanced]");
  }

  async expandAdvanced() {
    if ((await this.advancedSection().count()) === 0) return;
    const open = await this.advancedSection().getAttribute("open");
    if (open === null) {
      await this.advancedSection().locator("summary").click();
    }
  }

  async isAdvancedRendered() {
    return (await this.advancedSection().count()) > 0;
  }

  // ----- Submission -----------------------------------------------------

  async fillPrompt(text) {
    await this.page.locator("[data-test-prompt-input]").fill(text);
  }

  async clickSubmit() {
    await this.page.locator("[data-test-submit-button]").click();
  }

  async submitButtonDisabled() {
    return await this.page
      .locator("[data-test-submit-button]")
      .isDisabled();
  }

  async getError() {
    return (await this.page.locator("[data-test-error]").count()) > 0
      ? await this.page.locator("[data-test-error]").first().textContent()
      : null;
  }
}
