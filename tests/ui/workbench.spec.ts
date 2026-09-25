import { test, expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";

test.afterEach(async ({ page, request }, testInfo) => {
  if (testInfo.status === testInfo.expectedStatus) return;
  const id = await page.locator("#agent-design-id").textContent().catch(() => null);
  const state = id ? await request.get(`/api/designs/${id}`).then(r => r.json()).catch(String) : null;
  const diagnostics = {state,
    status: await page.locator("#build-status").textContent().catch(String),
    error: await page.locator("#error-details").textContent().catch(String),
    toast: await page.locator("#toast").textContent().catch(String)};
  console.log("Failed workbench state:", JSON.stringify(diagnostics));
  await testInfo.attach("workbench-state", {body: JSON.stringify(diagnostics, null, 2), contentType: "application/json"});
});

test.beforeEach(async ({ request }) => {
  await request.put("/api/workspace/active", {
    data: { design_id: "mounting-plate" },
  });
});

test("API-created designs appear automatically and pending edits are preserved", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await page.getByLabel("Width", { exact: true }).fill("91");
  const created = await request.post("/api/designs", {
    data: { name: "API pencil cup", template: "enclosure" },
  });
  const { id } = await created.json();
  await request.put("/api/workspace/active", { data: { design_id: id } });
  await expect(page.locator("#toast")).toContainText("Another design is ready");
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("91");
  await expect(page.locator("#design-name")).toHaveText("Mounting Plate");
  await page.getByRole("button", { name: "Reload saved parameters" }).click();
  await expect(page.locator("#design-name")).toHaveText("API pencil cup");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await expect(page.locator("#feature-list button")).toHaveCount(3);
  await page.reload();
  await expect(page.locator("#design-name")).toHaveText("API pencil cup");
});

test("model edits, feature steps, checkpoints, exports and reload persist", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await expect(page.locator("#feature-list button")).toHaveCount(4);
  await expect(page.locator("#model-stats")).toContainText("80 × 50 × 6");
  await page.getByRole("button", { name: "Base extrusion" }).click();
  await expect(page.locator("#model-stats")).toContainText("Earlier feature");
  await page.getByRole("button", { name: "Top edge chamfer" }).click();
  await page.getByRole("button", { name: "TOP", exact: true }).click();
  await page.getByRole("button", { name: "ISO", exact: true }).click();
  await page.getByLabel("Width", { exact: true }).fill("100");
  await page.getByRole("button", { name: "Apply changes" }).click();
  await expect(page.locator("#model-stats")).toContainText("100 × 50 × 6");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await page
    .getByRole("button", { name: "Save checkpoint", exact: true })
    .click();
  await page.getByLabel("Checkpoint name").fill("Wider plate");
  await page
    .locator("#checkpoint-form")
    .getByRole("button", { name: "Save checkpoint" })
    .click();
  await expect(page.locator("#modal")).not.toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("100");
  await expect(page.locator("#model-stats")).toContainText("100 × 50 × 6");
  await page.getByRole("button", { name: "Saved checkpoints" }).click();
  const beforeEdit = page
    .locator(".checkpoint")
    .filter({ hasText: "Before parameter edit" });
  await beforeEdit.getByRole("button", { name: "Restore" }).click();
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("80");
  await expect(page.locator("#model-stats")).toContainText("80 × 50 × 6");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await page.getByRole("button", { name: "Export", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "STL · 3D print mesh" }).click();
  expect((await download).suggestedFilename()).toBe("mounting-plate.stl");
  await expect(page.locator("#modal")).not.toBeVisible();
  await page.screenshot({ path: "test-results/workbench.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("new design, source and native solid templates work", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await page.getByRole("button", { name: "New design", exact: true }).click();
  await page.getByLabel("Design name").fill("Desk tray");
  await page.getByLabel("Starting point").selectOption("enclosure");
  await page
    .getByRole("button", { name: "Create design", exact: true })
    .click();
  await expect(page.locator("#design-name")).toHaveText("Desk tray");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await expect(page.locator("#feature-list button")).toHaveCount(3);
  await page.getByRole("button", { name: "View design source" }).click();
  await expect(page.locator(".source-block")).toContainText(".shell(-wall)");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByLabel("Current design").selectOption("turned-knob");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await expect(page.locator("#model-stats")).toContainText("36 × 36 × 22");
  await page.getByRole("button", { name: "Modeling guide" }).click();
  await expect(page.locator("#modal")).toContainText("Patterns & booleans");
});

test("invalid dimensions show a build error and recover", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await page.getByLabel("Hole inset", { exact: true }).fill("30");
  await page.getByRole("button", { name: "Apply changes" }).click();
  await expect(page.locator("#error-banner")).toBeVisible();
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "STL · 3D print mesh" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByLabel("Hole inset", { exact: true }).fill("10");
  await page.getByRole("button", { name: "Apply changes" }).click();
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await expect(page.locator("#error-banner")).not.toBeVisible();
});

test("Codex-style source edits rebuild live and protect pending dimensions", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/designs", {
    data: { name: "Live source test" },
  });
  const { id } = await created.json();
  await page.goto("/");
  await page.getByLabel("Current design").selectOption(id);
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await expect(page.locator("#model-stats")).toContainText("80 × 50 × 6");
  const state = await (await request.get(`/api/designs/${id}`)).json();
  const source = await readFile(state.source_path, "utf8");
  await page.getByLabel("Width", { exact: true }).fill("95");
  await writeFile(
    state.source_path,
    source.replace(".extrude(thickness)", ".extrude(thickness + 2)"),
  );
  await expect(page.locator("#model-stats")).toContainText("80 × 50 × 8");
  await expect(page.locator("#conflict-note")).toBeVisible();
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("95");
  await page.getByRole("button", { name: "Apply changes" }).click();
  await expect(page.locator("#toast")).toContainText("Design changed on disk");
  await page.getByRole("button", { name: "Reload saved parameters" }).click();
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("80");
  await expect(page.locator("#conflict-note")).not.toBeVisible();
});

test("blank models are saved without geometry and AI connection details follow selection", async ({page, request, context}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await page.getByRole("button", {name: "New design", exact: true}).click();
  await expect(page.getByLabel("Starting point")).toHaveValue("__blank");
  await page.getByLabel("Design name").fill("Fresh canvas");
  await page.getByRole("button", {name: "Create design", exact: true}).click();
  await expect(page.locator("#build-status")).toHaveText("Blank design · ready");
  const id = await page.getByLabel("Current design").inputValue();
  await expect(page.locator("#feature-list button")).toHaveCount(0);
  await expect(page.locator("#viewport-message")).toContainText("A blank canvas");
  await expect(page.locator("#model-stats")).toBeEmpty();
  await expect(page.locator("#parameter-fields input")).toHaveCount(0);
  await expect(page.locator("#error-banner")).not.toBeVisible();
  await expect(page.locator("#agent-design-id")).toHaveText(id);
  await expect(page.locator("#agent-api-url")).toHaveText(`http://127.0.0.1:8744/api/designs/${id}`);
  await page.getByRole("button", {name: "Copy AI connection instructions"}).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain(`"design_id": "${id}"`);
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain(`/api/designs/${id}/operations`);
  expect(copied).toContain("model.py");
  expect(copied).toContain("no source-upload");
  await page.getByRole("button", {name: "Export", exact: true}).click();
  await expect(page.getByRole("button", {name: "STL · 3D print mesh"})).toBeDisabled();
  const download = page.waitForEvent("download");
  await page.getByRole("button", {name: "Design archive · editable source"}).click();
  expect((await download).suggestedFilename()).toBe(`${id}-source.zip`);
  await page.reload();
  await expect(page.locator("#build-status")).toHaveText("Blank design · ready");
  await page.setViewportSize({width: 470, height: 950});
  await expect(page.getByRole("button", {name: "Copy AI connection instructions"})).toBeVisible();
  const state = await (await request.get(`/api/designs/${id}`)).json();
  const blankSource = await readFile(state.source_path, "utf8");
  await writeFile(state.source_path, "import cadquery as cq\ndef build(p):\n    return cq.Workplane('XY').box(12, 20, 4)\n");
  await expect(page.locator("#model-stats")).toContainText("12 × 20 × 4");
  await expect(page.locator("#viewport-message")).not.toBeVisible();
  await writeFile(state.source_path, blankSource);
  await expect(page.locator("#build-status")).toHaveText("Blank design · ready");
  await expect(page.locator("#model-stats")).toBeEmpty();
  await page.screenshot({path: "test-results/blank-model-mobile.png", fullPage: true});
  await page.getByLabel("Current design").selectOption("turned-knob");
  await expect(page.locator("#agent-design-id")).toHaveText("turned-knob");
  await page.getByRole("button", {name: "Copy AI connection instructions"}).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain('"design_id": "turned-knob"');
  expect(errors).toEqual([]);
});

test("AI instructions remain copyable when clipboard permission is unavailable", async ({page}) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {value: {writeText: async () => {throw new Error("Clipboard denied");}}});
  });
  await page.goto("/");
  await expect(page.locator("#build-status")).toContainText("Up to date");
  await page.getByLabel("Width", {exact: true}).fill("95");
  await page.getByRole("button", {name: "Copy AI connection instructions"}).click();
  await expect(page.getByLabel("AI connection instructions", {exact: true})).toBeVisible();
  await expect(page.getByLabel("AI connection instructions", {exact: true})).toHaveValue(/unapplied dimension edits/);
  await page.getByRole("button", {name: "Copy instructions", exact: true}).click();
  await expect(page.locator("#toast")).toContainText("Ctrl+C");
  await expect(page.getByLabel("Width", {exact: true})).toHaveValue("95");
});
