import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

test.use({ actionTimeout: 15000 });

async function blank(page: Page, request: APIRequestContext, name: string) {
  const response = await request.post("/api/designs", { data: { name, template: null } });
  expect(response.ok()).toBeTruthy();
  const { id } = await response.json();
  await request.put("/api/workspace/active", { data: { design_id: id } });
  await page.goto("/");
  await expect(page.locator("#design-name")).toHaveText(name);
  await expect(page.getByRole("button", { name: "Add feature", exact: true })).toBeEnabled();
  return id as string;
}

async function add(page: Page, type: string, name: string) {
  await page.getByRole("button", { name: "Add feature", exact: true }).click();
  await page.locator(`[data-add-type="${type}"]`).click();
  await page.getByLabel("Feature name", { exact: true }).fill(name);
}

async function apply(page: Page) {
  await page.getByRole("button", { name: "Apply feature", exact: true }).click();
  await expect(page.locator("#modal")).not.toBeVisible();
}

async function featureId(page: Page, name: string) {
  return (await page.locator("[data-definition]").filter({ hasText: name }).getAttribute("data-definition"))!;
}

test("human feature forms build, edit, suppress and delete validated geometry", async ({ page, request }) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const id = await blank(page, request, "Collaborative cup");
  await add(page, "profile", "Cup footprint");
  await page.getByLabel("Width (mm)", { exact: true }).fill("50");
  await page.getByLabel("Height (mm)", { exact: true }).fill("40");
  await expect(page.locator("#profile-preview")).toContainText("50.00 × 40.00 mm");
  await page.screenshot({ path: "test-results/structured-profile-editor.png", fullPage: true });
  await apply(page);
  await expect(page.locator("[data-definition]")).toHaveCount(1);
  await expect(page.locator("#model-stats")).toBeEmpty();
  await add(page, "extrude", "Cup body");
  await page.getByLabel("Distance (mm)", { exact: true }).fill("60");
  await apply(page);
  await expect(page.locator("#model-stats")).toContainText("50 × 40 × 60");
  await add(page, "fillet", "Rounded corners");
  await page.getByLabel("Radius (mm)", { exact: true }).fill("4");
  await page.getByLabel("Edges", { exact: true }).selectOption("Z");
  await apply(page);
  await add(page, "shell", "Open cup");
  await page.getByLabel("Wall thickness (mm)", { exact: true }).fill("2");
  await apply(page);
  await expect(page.locator("[data-definition]")).toHaveCount(4);
  await expect(page.locator("#solid-count")).toContainText("1 solid body");
  const shellId = await featureId(page, "Open cup");
  const graph = await (await request.get(`/api/designs/${id}/features`)).json();
  const state = await (await request.get(`/api/designs/${id}`)).json();
  expect(state.result.stats.volume).toBeLessThan(50000);
  await expect.poll(async () => {
    const trace = await (await request.get(`/api/traces/${state.trace_id}`)).json();
    return trace.viewer;
  }).toMatchObject({ revision: graph.revision, source: "browser_reported" });
  await page.getByRole("button", { name: "View design source", exact: true }).click();
  await expect(page.locator("#modal-title")).toHaveText("Editable feature definitions");
  await expect(page.locator(".source-block")).toContainText('"features"');
  await expect(page.locator(".source-block")).toContainText('"thickness": 2');
  await page.getByRole("button", { name: "Close dialog", exact: true }).click();
  await page.getByRole("button", { name: "Suppress", exact: true }).click();
  await expect(page.locator(`[data-definition="${shellId}"]`)).toContainText("suppressed");
  await page.getByRole("button", { name: "Unsuppress", exact: true }).click();
  await expect(page.locator(`[data-definition="${shellId}"]`)).not.toContainText("suppressed");
  await page.locator("[data-definition]").filter({ hasText: "Cup body" }).click();
  await page.getByRole("button", { name: "Edit feature", exact: true }).click();
  await page.getByLabel("Distance (mm)", { exact: true }).fill("70");
  await apply(page);
  await expect(page.locator("#model-stats")).toContainText("50 × 40 × 70");
  await page.locator(`[data-definition="${shellId}"]`).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("button", { name: "Delete feature", exact: true }).click();
  await expect(page.locator("#modal")).not.toBeVisible();
  await expect(page.locator("[data-definition]")).toHaveCount(3);
  // Reordering across a dependency must not silently remove a feature or save a broken model.
  const firstId = await featureId(page, "Cup footprint");
  await page.locator(`[data-definition="${firstId}"]`).click();
  await page.getByRole("button", { name: "Move feature later", exact: true }).click();
  await expect(page.locator("#toast")).toContainText(/reference|preced|before|earlier/i);
  expect((await (await request.get(`/api/designs/${id}/features`)).json()).features[0].id).toBe(firstId);
  await page.screenshot({ path: "test-results/structured-editor.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("agent revisions preserve an open human draft and selections round trip", async ({ page, request }) => {
  const id = await blank(page, request, "Revision collaboration");
  const initial = await (await request.get(`/api/designs/${id}/features`)).json();
  const seeded = await request.post(`/api/designs/${id}/operations`, { data: {
    expected_revision: initial.revision,
    actor: "agent",
    operations: [
      { op: "add", feature: { id: "outline", type: "profile", name: "Outline", params: { shape: "rectangle", width: 40, height: 30 } } },
      { op: "add", feature: { id: "body", type: "extrude", name: "Body", params: { profile: "outline", distance: 20 } } },
    ],
  } });
  expect(seeded.ok()).toBeTruthy();
  const { revision } = await seeded.json();
  await expect(page.locator("[data-definition]")).toHaveCount(2);
  await page.locator('[data-definition="body"]').click();
  await expect.poll(async () => (await (await request.get(`/api/designs/${id}/selection`)).json()).feature_id).toBe("body");
  await page.getByRole("button", { name: "Edit feature", exact: true }).click();
  await page.getByLabel("Distance (mm)", { exact: true }).fill("25");
  const edited = await request.post(`/api/designs/${id}/operations`, { data: {
    expected_revision: revision,
    actor: "agent",
    operations: [{ op: "update", id: "body", changes: { params: { distance: 35 } } }],
  } });
  expect(edited.ok()).toBeTruthy();
  const next = await edited.json();
  await expect(page.locator("#feature-conflict")).toBeVisible();
  await expect(page.getByLabel("Distance (mm)", { exact: true })).toHaveValue("25");
  const selection = await request.put(`/api/designs/${id}/selection`, { data: {
    expected_revision: next.revision, feature_id: "outline", actor: "agent", point: [0, 0, 0], normal: [0, 0, 1],
  } });
  expect(selection.ok()).toBeTruthy();
  // An agent selection arriving during a human draft is deferred, not discarded.
  await expect(page.locator('[data-definition="body"]')).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Apply feature", exact: true }).click();
  await expect(page.locator("#feature-form-error")).toContainText("newer revision");
  expect((await (await request.get(`/api/designs/${id}/features`)).json()).features[1].params.distance).toBe(35);
  await page.getByRole("button", { name: "Close dialog", exact: true }).click();
  await expect(page.locator('[data-definition="outline"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#selection-readout")).toContainText("Agent selected");
  // Surface picking publishes a geometric point and direction, never an unstable face index.
  await page.locator('[data-definition="body"]').click();
  await expect(page.locator("#model-stats")).toContainText("40 × 30 × 35");
  await page.getByRole("button", { name: "TOP", exact: true }).click();
  const viewport = page.locator("#viewport canvas");
  const bounds = (await viewport.boundingBox())!;
  await viewport.click({ position: { x: bounds.width / 2, y: bounds.height / 2 } });
  await expect.poll(async () => (await (await request.get(`/api/designs/${id}/selection`)).json()).point).toHaveLength(3);
  await page.getByRole("button", { name: "Measure between two surface points" }).click();
  await viewport.click({ position: { x: bounds.width / 2 - 10, y: bounds.height / 2 } });
  await viewport.click({ position: { x: bounds.width / 2 + 10, y: bounds.height / 2 } });
  await expect(page.locator("#selection-readout")).toContainText("Point-to-point distance:");
});

test("circle and boolean forms combine bodies and rejected geometry keeps the form", async ({ page, request }) => {
  test.setTimeout(120000);
  const id = await blank(page, request, "Boolean editor");
  await add(page, "profile", "Rectangle");
  await apply(page);
  await add(page, "extrude", "Plate");
  await apply(page);
  const plateId = await featureId(page, "Plate");
  await add(page, "profile", "Hole profile");
  await page.getByLabel("Profile shape", { exact: true }).selectOption("circle");
  await page.getByLabel("Radius (mm)", { exact: true }).fill("5");
  await apply(page);
  // Independent profiles can be reordered without rebuilding a broken dependency chain.
  await page.getByRole("button", { name: "Move feature earlier", exact: true }).click();
  await expect(page.locator("#toast")).toContainText("Reorder feature");
  await add(page, "extrude", "Cutter");
  await apply(page);
  const cutterId = await featureId(page, "Cutter");
  await expect(page.locator("#solid-count")).toContainText("2 solid bodies");
  await add(page, "boolean", "Cut hole");
  await page.getByLabel("Target feature", { exact: true }).selectOption(plateId);
  await page.getByLabel("Operation", { exact: true }).selectOption("cut");
  await page.locator(`input[name="tool"][value="${cutterId}"]`).check();
  await apply(page);
  await expect(page.locator("#solid-count")).toContainText("1 solid body");
  const before = await (await request.get(`/api/designs/${id}/features`)).json();
  await add(page, "fillet", "Impossible radius");
  await page.getByLabel("Radius (mm)", { exact: true }).fill("1000");
  await page.getByLabel("Edges", { exact: true }).selectOption("all");
  await page.getByRole("button", { name: "Apply feature", exact: true }).click();
  await expect(page.locator("#feature-form-error")).toBeVisible();
  await expect(page.getByLabel("Radius (mm)", { exact: true })).toHaveValue("1000");
  const after = await (await request.get(`/api/designs/${id}/features`)).json();
  expect(after.revision).toBe(before.revision);
  expect(after.features).toEqual(before.features);
});

test("a failed preview refresh never misreports or retries a committed edit", async ({ page, request }) => {
  const id = await blank(page, request, "Committed edit refresh");
  await add(page, "profile", "Saved profile");
  let failReads = false;
  let operationCalls = 0;
  await page.route(new RegExp(`/api/designs/${id}$`), async (route) => {
    if (failReads) await route.abort("connectionrefused");
    else await route.continue();
  });
  page.on("response", (response) => {
    if (response.url().endsWith(`/api/designs/${id}/operations`) && response.request().method() === "POST") {
      operationCalls++;
      if (response.ok()) failReads = true;
    }
  });
  await apply(page);
  await expect(page.locator("#collaboration-status")).toContainText("Saved and verified");
  await expect(page.locator("#collaboration-status")).toContainText("refresh");
  const graph = await (await request.get(`/api/designs/${id}/features`)).json();
  expect(graph.features).toHaveLength(1);
  expect(graph.features[0].name).toBe("Saved profile");
  expect(operationCalls).toBe(1);
  failReads = false;
  await expect(page.locator("[data-definition]")).toHaveCount(1);
  expect(operationCalls).toBe(1);
});
