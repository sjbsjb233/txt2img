/**
 * Archive drawer layout tests at 1710×985 viewport.
 * Tests both sidebar-expanded and sidebar-collapsed (rail) states.
 */

import { test, expect } from "@playwright/test";
import {
  setupArchiveMocks,
  makeFullInitScript,
  makeFakeJobs,
} from "./helpers/mockArchive.js";

const DIR = "e2e/screenshots/1710";
const VP = { width: 1710, height: 985 };

async function openArchivePage(page, jobs) {
  await page.addInitScript(makeFullInitScript(jobs));
  await setupArchiveMocks(page, jobs);
  await page.goto("/archive");
  await page.waitForSelector('[data-testid="archive-card"]', { timeout: 20000 });
  await page.waitForTimeout(600);
}

async function measureCards(page) {
  return page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('[data-testid="archive-card"]'));
    return cards.map((el) => {
      const r = el.getBoundingClientRect();
      return { width: Math.round(r.width), height: Math.round(r.height) };
    });
  });
}

async function collapseSidebar(page) {
  // Click the double-chevron-left button (collapse button in Sidebar)
  await page.locator('button[title="Collapse sidebar"], button.nav-btn-expanded ~ div button, button:has([data-icon="double-chevron-left"])').first().click().catch(() => {});
  // Fallback: find the << icon button at the bottom of the expanded sidebar
  const collapseBtn = page.locator('button').filter({ hasText: '' }).locator('nth=-2');
  // Try the title attribute approach
  const btnByTitle = page.locator('button[title="Collapse sidebar"]');
  if (await btnByTitle.count() > 0) {
    await btnByTitle.click();
  } else {
    // Find button containing the double-chevron icon near the sidebar bottom
    await page.evaluate(() => {
      // Find all buttons in the sidebar (left nav)
      const sidebar = document.querySelector('nav, [class*="sidebar"], [class*="Sidebar"]') ||
        document.querySelector('[style*="position: fixed"][style*="left"]') ||
        document.querySelector('[style*="width: 232px"]');
      if (!sidebar) return;
      const btns = sidebar.querySelectorAll('button');
      // The collapse button is the last button in expanded mode
      const last = btns[btns.length - 1];
      if (last) last.click();
    });
  }
  await page.waitForTimeout(400);
}

async function expandSidebar(page) {
  // In rail mode, click the user initials button at the bottom to expand
  const railFoot = page.locator('.rail-foot');
  if (await railFoot.count() > 0) {
    await railFoot.click();
  }
  await page.waitForTimeout(400);
}

// ── Test 1: 1710px with sidebar expanded ─────────────────────────────────────
test("1710px sidebar-expanded: baseline → drawer open → closed", async ({ page }) => {
  await page.setViewportSize(VP);
  const jobs = makeFakeJobs(16);
  await openArchivePage(page, jobs);

  // 1. Baseline
  const before = await measureCards(page);
  await page.screenshot({ path: `${DIR}/expanded_1_baseline.png` });
  console.log(`[1710 expanded] Baseline: ${before.length} cards, ${before[0].width}×${before[0].height}px`);

  // 2. Open drawer
  await page.locator('[data-testid="archive-card"]').first().click();
  await page.waitForTimeout(500);
  const after = await measureCards(page);
  await page.screenshot({ path: `${DIR}/expanded_2_drawer_open.png` });
  console.log(`[1710 expanded] Drawer open: ${after[0].width}×${after[0].height}px (delta: ${after[0].width - before[0].width}px)`);

  // 3. Scroll down
  await page.evaluate(() => {
    const scroller = document.querySelector('[data-testid="archive-grid"]')?.closest('[style*="overflow"]');
    if (scroller) scroller.scrollTop = 300;
  });
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${DIR}/expanded_3_drawer_scrolled.png` });

  // 4. Close drawer
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);
  const closed = await measureCards(page);
  await page.screenshot({ path: `${DIR}/expanded_4_drawer_closed.png` });
  console.log(`[1710 expanded] After close: ${closed[0].width}px`);

  // Cards must not grow when drawer opens
  expect(after[0].width).toBeLessThanOrEqual(before[0].width);
  // Cards must return to baseline after close
  expect(Math.abs(closed[0].width - before[0].width)).toBeLessThanOrEqual(2);
});

// ── Test 2: 1710px with sidebar collapsed (rail mode) ───────────────────────
test("1710px sidebar-rail: baseline → drawer open → closed", async ({ page }) => {
  await page.setViewportSize(VP);
  const jobs = makeFakeJobs(16);
  await openArchivePage(page, jobs);

  // Collapse the sidebar first
  await page.screenshot({ path: `${DIR}/rail_0_before_collapse.png` });

  // Find and click the collapse button (double-chevron-left)
  // It's inside the expanded sidebar footer area
  const collapseBtn = page.locator('button[title="Collapse sidebar"]');
  if (await collapseBtn.count() > 0) {
    await collapseBtn.click();
  } else {
    // The collapse button has an Icon with double-chevron-left — look for bottom-of-sidebar button
    await page.evaluate(() => {
      const allBtns = [...document.querySelectorAll('button')];
      // Sidebar is fixed on left; collapse btn is in the bottom-left area
      const sidebarBtns = allBtns.filter(b => {
        const r = b.getBoundingClientRect();
        return r.left < 250 && r.top > 600;
      });
      if (sidebarBtns.length > 0) sidebarBtns[sidebarBtns.length - 1].click();
    });
  }
  await page.waitForTimeout(400);

  await page.screenshot({ path: `${DIR}/rail_1_sidebar_collapsed.png` });

  // 1. Baseline with rail sidebar
  const before = await measureCards(page);
  console.log(`[1710 rail] Baseline: ${before.length} cards, ${before[0].width}×${before[0].height}px`);

  // 2. Open drawer
  await page.locator('[data-testid="archive-card"]').first().click();
  await page.waitForTimeout(500);
  const after = await measureCards(page);
  await page.screenshot({ path: `${DIR}/rail_2_drawer_open.png` });
  console.log(`[1710 rail] Drawer open: ${after[0].width}×${after[0].height}px (delta: ${after[0].width - before[0].width}px)`);

  // 3. Scroll down
  await page.evaluate(() => {
    const scroller = document.querySelector('[data-testid="archive-grid"]')?.closest('[style*="overflow"]');
    if (scroller) scroller.scrollTop = 300;
  });
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${DIR}/rail_3_drawer_scrolled.png` });

  // 4. Close drawer
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);
  const closed = await measureCards(page);
  await page.screenshot({ path: `${DIR}/rail_4_drawer_closed.png` });
  console.log(`[1710 rail] After close: ${closed[0].width}px`);

  // Cards must not grow
  expect(after[0].width).toBeLessThanOrEqual(before[0].width);
  expect(Math.abs(closed[0].width - before[0].width)).toBeLessThanOrEqual(2);
});

// ── Test 3: sidebar expand/collapse while drawer is open ────────────────────
test("1710px: toggle sidebar while drawer is open", async ({ page }) => {
  await page.setViewportSize(VP);
  const jobs = makeFakeJobs(12);
  await openArchivePage(page, jobs);

  // Open drawer
  await page.locator('[data-testid="archive-card"]').first().click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${DIR}/toggle_1_drawer_open_expanded.png` });

  const w1 = (await measureCards(page))[0].width;
  console.log(`[1710 toggle] Drawer open + sidebar expanded: card width = ${w1}px`);

  // Collapse sidebar while drawer is open
  const collapseBtn = page.locator('button[title="Collapse sidebar"]');
  if (await collapseBtn.count() > 0) {
    await collapseBtn.click();
  } else {
    await page.evaluate(() => {
      const allBtns = [...document.querySelectorAll('button')];
      const sidebarBtns = allBtns.filter(b => {
        const r = b.getBoundingClientRect();
        return r.left < 250 && r.top > 600;
      });
      if (sidebarBtns.length > 0) sidebarBtns[sidebarBtns.length - 1].click();
    });
  }
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/toggle_2_drawer_open_rail.png` });

  const w2 = (await measureCards(page))[0].width;
  console.log(`[1710 toggle] Drawer open + sidebar rail: card width = ${w2}px`);

  // Re-expand sidebar while drawer is still open
  const railFoot = page.locator('.rail-foot');
  if (await railFoot.count() > 0) {
    await railFoot.click();
    await page.waitForTimeout(400);
  }
  await page.screenshot({ path: `${DIR}/toggle_3_drawer_open_re_expanded.png` });

  const w3 = (await measureCards(page))[0].width;
  console.log(`[1710 toggle] Drawer open + sidebar re-expanded: card width = ${w3}px`);

  // Close drawer
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/toggle_4_all_closed.png` });

  console.log(`[1710 toggle] Summary: expanded=${w1}px → rail=${w2}px → re-expanded=${w3}px`);
});
