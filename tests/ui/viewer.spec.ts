import { test, expect } from "@playwright/test";

test("idle geometry stops GPU drawing and camera/grid interactions redraw", async ({ page, request }) => {
  await page.addInitScript(() => {
    const counter = window as unknown as { gpuDraws: number };
    counter.gpuDraws = 0;
    for (const context of [WebGLRenderingContext, WebGL2RenderingContext]) {
      const draw = context.prototype.drawElements;
      context.prototype.drawElements = function (...args: Parameters<typeof draw>) {
        counter.gpuDraws++;
        return draw.apply(this, args);
      };
    }
  });
  await request.put("/api/workspace/active", { data: { design_id: "mounting-plate" } });
  await page.goto("/");
  await expect(page.locator("#model-stats")).toContainText("80 × 50 × 6");
  const draws = () => page.evaluate(() => (window as unknown as { gpuDraws: number }).gpuDraws);
  await expect.poll(draws).toBeGreaterThan(0);
  // Let the initial fit/resize settle, then observe real GPU draw calls while
  // normal API polling continues. A continuous animation loop fails this check.
  await page.waitForTimeout(500);
  const settled = await draws();
  await page.waitForTimeout(1200);
  expect(await draws()).toBe(settled);
  await page.locator("#grid-button").click();
  await expect.poll(draws).toBeGreaterThan(settled);
  const beforeCamera = await draws();
  await page.getByRole("button", { name: "TOP", exact: true }).click();
  await expect.poll(draws).toBeGreaterThan(beforeCamera);
  const beforeIso = await draws();
  await page.getByRole("button", { name: "ISO", exact: true }).click();
  await expect.poll(draws).toBeGreaterThan(beforeIso);
  const canvas = page.locator("#viewport canvas");
  const box = (await canvas.boundingBox())!;
  const beforeOrbit = await draws();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 80, box.y + box.height / 2 + 35, { steps: 8 });
  await page.mouse.up();
  await expect.poll(draws).toBeGreaterThan(beforeOrbit);
});
