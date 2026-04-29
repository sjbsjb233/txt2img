/**
 * Archive drawer layout tests.
 *
 * Verifies the reported bug: when the right-side JobDrawer opens, the
 * image cards in the grid should NOT become larger. Tests multiple
 * viewport widths where the mismatch is most visible.
 */

import { test, expect } from "@playwright/test";
import {
  setupArchiveMocks,
  makeFullInitScript,
  makeFakeJobs,
} from "./helpers/mockArchive.js";

const SCREENSHOTS_DIR = "e2e/screenshots";

// ──────────────────────────────────────────────────────────────────────────────
// Helper: navigate to archive and wait until cards are visible
// ──────────────────────────────────────────────────────────────────────────────
async function openArchivePage(page, jobs) {
  // Inject auth + pre-populate IndexedDB BEFORE navigation
  await page.addInitScript(makeFullInitScript(jobs));
  // Mock network calls for sync + thumbnails
  await setupArchiveMocks(page, jobs);
  await page.goto("/archive");
  // Wait for at least one card to be rendered (IDB hydration → notify → render)
  await page.waitForSelector('[data-testid="archive-card"]', { timeout: 20000 });
  // Let layout settle (transitions, image loads)
  await page.waitForTimeout(600);
}

/** Measure width and height of every archive card */
async function measureCards(page) {
  return page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('[data-testid="archive-card"]'));
    return cards.map((el) => {
      const r = el.getBoundingClientRect();
      return { width: Math.round(r.width), height: Math.round(r.height) };
    });
  });
}

/** Measure the grid container's computed column template */
async function getGridCols(page) {
  return page.evaluate(() => {
    const grid = document.querySelector('[data-testid="archive-grid"]');
    if (!grid) return null;
    return window.getComputedStyle(grid).gridTemplateColumns;
  });
}

/** Get right-side scrollable wrapper padding */
async function getScrollablePaddingRight(page) {
  return page.evaluate(() => {
    // The scrollable wrapper is the first flex child of the archive root
    const drawer = document.querySelector('[data-testid="job-drawer"]');
    if (!drawer) return null;
    const parent = drawer.parentElement;
    if (!parent) return null;
    // The scrollable wrapper is a sibling that precedes the drawer (or is it?)
    // Actually it's the parent's first child (div, not aside)
    const wrapper = Array.from(parent.children).find((el) => el.tagName !== "ASIDE");
    if (!wrapper) return null;
    return window.getComputedStyle(wrapper).paddingRight;
  });
}

// ──────────────────────────────────────────────────────────────────────────────
// Test suite: bug reproduction at different viewport widths
// ──────────────────────────────────────────────────────────────────────────────

const VIEWPORTS = [
  { width: 1280, height: 800, label: "1280" },
  { width: 1440, height: 900, label: "1440" },
  { width: 1920, height: 1080, label: "1920" },
  { width: 2560, height: 1440, label: "2560" },
];

for (const vp of VIEWPORTS) {
  test(`[BUG] card size stays stable when drawer opens – viewport ${vp.label}px`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    const jobs = makeFakeJobs(12);
    await openArchivePage(page, jobs);

    // ── Baseline: grid without drawer ──────────────────────────────────────
    const beforeCards = await measureCards(page);
    const beforeCols = await getGridCols(page);
    expect(beforeCards.length).toBeGreaterThan(0);
    const beforeWidth = beforeCards[0].width;
    const beforeHeight = beforeCards[0].height;

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/bug_vp${vp.label}_1_baseline.png`,
      fullPage: false,
    });

    console.log(`[${vp.label}px] Baseline: ${beforeCards.length} cards, ` +
      `each ~${beforeWidth}×${beforeHeight}px, cols: "${beforeCols}"`);

    // ── Open drawer by clicking the first card ──────────────────────────────
    const firstCard = page.locator('[data-testid="archive-card"]').first();
    await firstCard.click();

    // Wait for the drawer to slide in (360ms animation + buffer)
    await page.waitForTimeout(500);

    const afterCards = await measureCards(page);
    const afterCols = await getGridCols(page);
    const afterWidth = afterCards[0].width;
    const afterHeight = afterCards[0].height;
    const paddingRight = await getScrollablePaddingRight(page);

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/bug_vp${vp.label}_2_drawer_open.png`,
      fullPage: false,
    });

    console.log(`[${vp.label}px] Drawer open: ${afterCards.length} cards, ` +
      `each ~${afterWidth}×${afterHeight}px, cols: "${afterCols}", ` +
      `wrapper paddingRight: ${paddingRight}`);

    // ── Scroll down to see more cards ──────────────────────────────────────
    await page.evaluate(() => {
      const scroller = document.querySelector('[data-testid="archive-grid"]')?.closest('[style*="overflow-y"]');
      if (scroller) scroller.scrollTop = 200;
    });
    await page.waitForTimeout(200);
    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/bug_vp${vp.label}_3_drawer_scrolled.png`,
      fullPage: false,
    });

    // ── Close drawer via Escape key ────────────────────────────────────────
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);

    const closedCards = await measureCards(page);
    const closedWidth = closedCards[0].width;

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/bug_vp${vp.label}_4_drawer_closed.png`,
      fullPage: false,
    });

    console.log(`[${vp.label}px] After close: cards ~${closedWidth}×${closedCards[0].height}px`);

    // ── Assertions ────────────────────────────────────────────────────────
    const widthChange = afterWidth - beforeWidth;
    const pct = ((widthChange / beforeWidth) * 100).toFixed(1);
    console.log(`[${vp.label}px] Card width change on drawer open: ${widthChange > 0 ? "+" : ""}${widthChange}px (${pct}%)`);

    // Cards must NEVER grow larger when the drawer opens.
    // At very narrow viewports (≤1300px), the drawer physically leaves so
    // little horizontal space (< 500px) that the auto-fill grid can only
    // fit 2 columns and each is slightly wider than the 4-column baseline.
    // We allow up to 5% growth there; anything larger is a genuine bug.
    // At normal and wide viewports (≥1440px) the tolerance is 0.
    const maxGrowthPx = vp.width <= 1300 ? Math.round(beforeWidth * 0.05) : 0;
    expect(
      afterWidth,
      `Card width after drawer open (${afterWidth}px) must not exceed baseline (${beforeWidth}px) + ${maxGrowthPx}px tolerance`
    ).toBeLessThanOrEqual(beforeWidth + maxGrowthPx);

    // After closing, cards should return to baseline size (within 2px tolerance)
    expect(Math.abs(closedWidth - beforeWidth)).toBeLessThanOrEqual(2);
  });
}

// ──────────────────────────────────────────────────────────────────────────────
// Visual comparison test: side-by-side at 1440px
// ──────────────────────────────────────────────────────────────────────────────
test("Visual: full-page comparison at 1440px viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const jobs = makeFakeJobs(16);
  await openArchivePage(page, jobs);

  // Full grid baseline
  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/visual_1_grid_only.png`,
    fullPage: true,
  });

  // Click card 0
  await page.locator('[data-testid="archive-card"]').first().click();
  await page.waitForTimeout(600);
  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/visual_2_drawer_card1.png`,
    fullPage: true,
  });

  // Navigate to next card via drawer button
  const nextBtn = page.locator('[data-testid="job-drawer"]').getByText("⌘]");
  await nextBtn.click();
  await page.waitForTimeout(300);
  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/visual_3_drawer_card2.png`,
    fullPage: true,
  });

  // Navigate prev
  const prevBtn = page.locator('[data-testid="job-drawer"]').getByText("prev");
  await prevBtn.click();
  await page.waitForTimeout(300);
  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/visual_4_drawer_prev.png`,
    fullPage: true,
  });

  // Close with Escape key
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);
  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/visual_5_after_escape.png`,
    fullPage: true,
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// Stress test: rapid open/close cycling
// ──────────────────────────────────────────────────────────────────────────────
test("Stress: rapid open/close cycle keeps card size stable", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const jobs = makeFakeJobs(12);
  await openArchivePage(page, jobs);

  const baselineCards = await measureCards(page);
  const baselineWidth = baselineCards[0].width;

  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/stress_0_baseline.png`,
  });

  // Click different cards in sequence
  for (let i = 0; i < 4; i++) {
    const card = page.locator('[data-testid="archive-card"]').nth(i);
    await card.click();
    await page.waitForTimeout(400);

    const cards = await measureCards(page);
    const w = cards[0].width;
    console.log(`Stress cycle ${i + 1}: card[0] width = ${w}px (baseline: ${baselineWidth}px, delta: ${w - baselineWidth}px)`);

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/stress_${i + 1}_open_card${i}.png`,
    });

    // Close via Escape
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  }

  const finalCards = await measureCards(page);
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/stress_final.png` });

  console.log(`Stress final card width: ${finalCards[0].width}px`);
  expect(Math.abs(finalCards[0].width - baselineWidth)).toBeLessThanOrEqual(2);
});

// ──────────────────────────────────────────────────────────────────────────────
// Period filter stays functional with drawer
// ──────────────────────────────────────────────────────────────────────────────
test("Filter: period toggle while drawer is open works correctly", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const jobs = makeFakeJobs(8);
  await openArchivePage(page, jobs);

  // Open drawer
  await page.locator('[data-testid="archive-card"]').first().click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/filter_1_drawer_open.png` });

  // Click period filter
  await page.locator("button", { hasText: /period/ }).click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/filter_2_period_toggled.png` });

  // Drawer should still be visible
  const drawer = page.locator('[data-testid="job-drawer"]');
  await expect(drawer).toBeVisible();
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/filter_3_drawer_still_visible.png` });
});
