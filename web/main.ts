import "./style.css";
import {
  createIcons,
  Box,
  FolderOpen,
  Plus,
  BookmarkPlus,
  Download,
  ChevronDown,
  History,
  ChevronRight,
  AudioLines,
  FileCode2,
  ArrowUpRight,
  Component,
  BookOpen,
  Maximize,
  ScanLine,
  Grid2x2,
  SlidersHorizontal,
  Check,
  RotateCcw,
  Info,
  HardDrive,
  TriangleAlert,
  X,
  Layers,
  Circle,
  CircleDot,
  Spline,
  Shell,
  Rotate3d,
  Package,
  Copy,
  FileArchive,
  Ruler,
} from "lucide";
import { Viewer, type MeshData, type SurfacePick } from "./viewer";

const icons = {
  Box,
  FolderOpen,
  Plus,
  BookmarkPlus,
  Download,
  ChevronDown,
  History,
  ChevronRight,
  AudioLines,
  FileCode2,
  ArrowUpRight,
  Component,
  BookOpen,
  Maximize,
  ScanLine,
  Grid2x2,
  SlidersHorizontal,
  Check,
  RotateCcw,
  Info,
  HardDrive,
  TriangleAlert,
  X,
  Layers,
  Circle,
  CircleDot,
  Spline,
  Shell,
  Rotate3d,
  Package,
  Copy,
  FileArchive,
  Ruler,
};
const refreshIcons = () => createIcons({ icons });
const $ = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.getElementById(id) as T;
const escape = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
const icon = (name: string) => `<i data-lucide="${name}"></i>`;

interface Parameter {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  description: string;
}
interface Feature {
  id?: string;
  index: number;
  name: string;
  operation: string;
  description: string;
  bounds: number[];
  volume: number;
  solids: number;
}
interface State {
  id: string;
  fingerprint: string;
  status: "building" | "ready" | "error";
  error: string | null;
  seconds: number | null;
  trace_id?: string | null;
  timings?: Record<string, number | boolean | string>;
  source_path: string;
  design: {
    name: string;
    description: string;
    parameters: Record<string, Parameter>;
    print_notes: string;
    features?: Definition[] | null;
  };
  result: { features: Feature[]; stats: Feature | null; empty?: boolean } | null;
}

type FeatureType = "profile" | "extrude" | "fillet" | "shell" | "boolean";
interface Definition {
  id: string;
  type: FeatureType;
  name?: string;
  suppressed?: boolean;
  params: Record<string, unknown>;
}
interface SharedSelection {
  revision: string;
  feature_id?: string | null;
  point?: number[] | null;
  normal?: number[] | null;
  actor?: string;
}
interface FeatureGraph {
  revision: string;
  mode: "structured" | "python";
  features: Definition[];
  parameters: Record<string, Parameter>;
  selection?: SharedSelection | null;
}
interface VerifiedEdit {
  ok: boolean;
  revision: string;
  body_count: number;
  bounds: number[] | null;
  timings: Record<string, number>;
  trace_id?: string;
}
interface Capabilities {
  operations: Record<string, { description: string; constraints?: string[] }>;
}

let currentId = localStorage.getItem("voicedesign.current") || "mounting-plate";
let current: State | null = null;
let formFingerprint = "";
let dirty = false;
let selectedFeature = -1;
let renderedKey = "";
let meshKey = "";
let meshGeneration = 0;
let pollRunning = false;
let applying = false;
let selecting = false;
let selectionNotice = -1;
let toastTimer = 0;
let graph: FeatureGraph | null = null;
let graphKey = "";
let graphGeneration = 0;
let selectedDefinition = "";
let featureSaving = false;
let capabilities: Capabilities | null = null;
let sharedSelectionKey = "";
let measurementEnabled = false;
let events: EventSource | null = null;
let latestEditTiming: { revision: string; api_ms: number } | null = null;
let latestViewerTiming: { revision: string; load_ms: number; render_ms: number } | null = null;
const reportedTraces = new Set<string>();
const modal = $<HTMLDialogElement>("modal");
let viewer: Viewer | null = null;
try {
  viewer = new Viewer($("viewport"));
} catch (error) {
  $("viewport-message").textContent = `3D preview needs WebGL: ${error}`;
}

class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: `Request failed (${response.status}).` }));
    if (Array.isArray(error.feature_errors) && error.feature_errors.length) {
      throw new ApiError(error.feature_errors.map((item: { feature_id?: string; message: string }) => {
        const name = graph?.features.find((feature) => feature.id === item.feature_id)?.name || item.feature_id;
        return `${name ? `${name}: ` : ""}${item.message}`;
      }).join("\n"), response.status);
    }
    throw new ApiError(
      typeof error.detail === "string"
        ? error.detail
        : JSON.stringify(error.detail), response.status,
    );
  }
  return response.json();
}

function toast(message: string, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  toastTimer = window.setTimeout(
    () => ($("toast").hidden = true),
    error ? 9000 : 4000,
  );
}

function showModal(title: string, content: string) {
  $("modal-content").onclick = null;
  $("modal-title").textContent = title;
  $("modal-content").innerHTML = content;
  refreshIcons();
  if (!modal.open) modal.showModal();
}
$("close-modal").onclick = () => modal.close();
modal.addEventListener("click", (event) => {
  if (event.target === modal) {
    const r = modal.getBoundingClientRect();
    if (
      event.clientX < r.left ||
      event.clientX > r.right ||
      event.clientY < r.top ||
      event.clientY > r.bottom
    )
      modal.close();
  }
});

async function loadDesigns() {
  const designs = await api<{ id: string; name: string }[]>("/designs");
  if (!designs.some((d) => d.id === currentId))
    currentId = designs[0]?.id || "";
  $("design-select").innerHTML = designs
    .map((d) => `<option value="${escape(d.id)}">${escape(d.name)}</option>`)
    .join("");
  $<HTMLSelectElement>("design-select").value = currentId;
  if (!currentId) {
    $("viewport-message").textContent =
      "Create a design to start your local workspace.";
    $("design-name").textContent = "No designs yet";
  }
}

async function selectDesign(id: string, publish = true) {
  if (publish) {
    selecting = true;
    try {
      await api("/workspace/active", {
        method: "PUT",
        body: JSON.stringify({ design_id: id }),
      });
    } catch (error) {
      $<HTMLSelectElement>("design-select").value = currentId;
      toast(String(error), true);
      return;
    } finally {
      selecting = false;
    }
  }
  currentId = id;
  localStorage.setItem("voicedesign.current", id);
  current = null;
  graph = null;
  graphKey = "";
  graphGeneration++;
  selectedDefinition = "";
  sharedSelectionKey = "";
  latestEditTiming = null;
  latestViewerTiming = null;
  $("latency-details").hidden = true;
  $("feature-actions").hidden = true;
  $<HTMLButtonElement>("add-feature").disabled = true;
  $("selection-readout").hidden = true;
  $("agent-design-id").textContent = id;
  $("agent-api-url").textContent = `${location.origin}/api/designs/${encodeURIComponent(id)}`;
  $<HTMLButtonElement>("copy-agent-context").disabled = true;
  $<HTMLButtonElement>("view-agent-context").disabled = true;
  dirty = false;
  formFingerprint = "";
  renderedKey = "";
  meshKey = "";
  selectedFeature = -1;
  meshGeneration++;
  viewer?.clear();
  $("model-stats").innerHTML = "";
  $("build-status").textContent = "Opening…";
  $("feature-list").innerHTML = "";
  $("parameter-fields").innerHTML = "";
  $("error-banner").hidden = true;
  $<HTMLSelectElement>("design-select").value = id;
  $("viewport-message").hidden = false;
  $("viewport-message").textContent = "Opening design…";
  await poll();
}

$<HTMLSelectElement>("design-select").onchange = async (event) => {
  const next = (event.target as HTMLSelectElement).value;
  if (
    dirty &&
    !window.confirm("Discard unapplied parameter changes and switch designs?")
  ) {
    $<HTMLSelectElement>("design-select").value = currentId;
    return;
  }
  await selectDesign(next);
};

function renderParameters(state: State) {
  $("parameter-fields").innerHTML = Object.entries(state.design.parameters)
    .map(
      ([key, p]) => `
    <div class="parameter-field"><label for="param-${escape(key)}">${escape(p.label)}</label>
    <div class="number-control"><input id="param-${escape(key)}" name="${escape(key)}" type="number" value="${p.value}" min="${p.min}" max="${p.max}" step="${p.step}" required aria-label="${escape(p.label)}"><span>${escape(p.unit)}</span></div>
    ${p.description ? `<small>${escape(p.description)}</small>` : ""}</div>`,
    )
    .join("");
  const hasParameters = Object.keys(state.design.parameters).length > 0;
  if (!hasParameters) $("parameter-fields").innerHTML = '<p class="empty-state">No named parameters yet. Select a feature to edit its dimensions.</p>';
  $("parameter-form").querySelector<HTMLElement>(".parameter-actions")!.hidden = !hasParameters;
  formFingerprint = state.fingerprint;
  dirty = false;
  $<HTMLButtonElement>("apply-button").disabled = true;
  $("conflict-note").hidden = true;
  $("parameter-status").textContent = hasParameters ? "Changes are saved locally when applied." : "Your design is saved locally.";
  $("footer-status").textContent =
    state.status === "ready" ? "All changes saved locally" : "Rebuilding…";
}

function render(state: State) {
  const changed = current?.fingerprint !== state.fingerprint;
  current = state;
  const empty = state.status === "ready" && !!state.result?.empty;
  $("agent-design-id").textContent = state.id;
  $("agent-api-url").textContent = `${location.origin}/api/designs/${encodeURIComponent(state.id)}`;
  $("agent-revision").textContent = state.fingerprint.slice(0, 12);
  $("agent-revision").title = state.fingerprint;
  renderTimings(state);
  if (modal.open && $("feature-form")) {
    const stale = $<HTMLFormElement>("feature-form").dataset.revision !== state.fingerprint;
    $("feature-conflict").hidden = !stale;
  }
  $<HTMLButtonElement>("copy-agent-context").disabled = false;
  $<HTMLButtonElement>("view-agent-context").disabled = false;
  $("design-name").textContent = state.design.name;
  $("design-description").textContent = state.design.description;
  $("print-notes").textContent = state.design.print_notes;
  $("source-path").textContent = state.source_path;
  $("source-path").title = state.source_path;
  $("model-subtitle").textContent = state.design.name.toUpperCase();
  const status =
    state.status === "ready"
      ? empty ? "Blank design · ready" : `Up to date · ${state.seconds}s`
      : state.status === "building"
        ? "Rebuilding…"
        : "Build needs attention";
  $("build-status").textContent = status;
  $("footer-status").textContent = dirty
    ? "Unapplied parameter changes"
    : state.status === "ready"
      ? "All changes saved locally"
      : status;
  $("error-banner").hidden = state.status !== "error";
  $("error-details").textContent = state.error || "";
  if (!dirty && (changed || !formFingerprint)) renderParameters(state);
  if (dirty) $("conflict-note").hidden = formFingerprint === state.fingerprint;
  if (viewer) {
    $("viewport-message").hidden =
      (state.status === "ready" && !empty) || (state.status === "error" && !!meshKey);
    $("viewport-message").textContent =
      empty ? "A blank canvas. Tell your AI what you want to make."
      : state.status === "building"
        ? "Rebuilding solid geometry…"
        : "Fix the model or restore a checkpoint.";
  }
  const key = state.fingerprint + state.status;
  if (key !== renderedKey) {
    renderedKey = key;
    if (empty) {
      meshGeneration++;
      meshKey = "";
      selectedFeature = -1;
      viewer?.clear();
      $("model-stats").innerHTML = "";
      $("feature-count").textContent = "0";
      $("feature-list").innerHTML = '<div class="empty-state">Your first feature will appear here.</div>';
      $("solid-count").textContent = "No geometry yet";
    } else if (state.result) {
      selectedFeature = state.result.features.length - 1;
      renderFeatures();
      void loadMesh(selectedFeature, !meshKey);
    } else {
      $("feature-count").textContent = "—";
      $("feature-list").innerHTML =
        `<div class="empty-state">${state.status === "building" ? "Building feature history…" : "Feature history will return when the design builds successfully."}</div>`;
      $("solid-count").textContent = "Parametric part";
    }
  }
  void syncFeatureGraph(state);
  applySharedSelection(graph?.selection);
}

const operationIcons: Record<string, string> = {
  extrude: "layers",
  fillet: "spline",
  hole: "circle-dot",
  chamfer: "scan-line",
  shell: "shell",
  revolve: "rotate-3d",
  loft: "component",
  sweep: "spline",
  feature: "box",
};
function renderFeatures() {
  if (graph?.mode === "structured" && graph.revision === current?.fingerprint) {
    renderStructuredFeatures();
    return;
  }
  if (!current?.result?.stats) return;
  const features = current.result.features;
  $("feature-count").textContent = String(features.length);
  $("solid-count").textContent =
    `${current.result.stats.solids} solid ${current.result.stats.solids === 1 ? "body" : "bodies"} · ${features.length} features`;
  $("feature-list").innerHTML = features
    .map(
      (f, i) =>
        `<button class="feature-row ${i === selectedFeature ? "active" : ""}" data-feature="${i}" title="${escape(f.description || f.operation)}" aria-pressed="${i === selectedFeature}">${icon(operationIcons[f.operation] || "box")}<span>${escape(f.name)}</span><small>${String(i + 1).padStart(2, "0")}</small></button>`,
    )
    .join("");
  refreshIcons();
}

$("feature-list").onclick = (event) => {
  const structuredButton = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-definition]");
  if (structuredButton) {
    selectDefinition(structuredButton.dataset.definition!, true);
    return;
  }
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>(
    "[data-feature]",
  );
  if (!button || current?.status !== "ready") return;
  selectedFeature = Number(button.dataset.feature);
  renderFeatures();
  void loadMesh(selectedFeature, false);
};

async function loadMesh(index: number, reset: boolean) {
  if (!current || !viewer) return;
  const fingerprint = current.fingerprint,
    id = currentId;
  const key = `${id}:${fingerprint}:${index}`,
    generation = ++meshGeneration;
  if (key === meshKey) return;
  try {
    const start = performance.now();
    const data = await api<MeshData>(
      `/designs/${id}/mesh/${index}?fingerprint=${fingerprint}`,
    );
    if (
      id !== currentId ||
      fingerprint !== current?.fingerprint ||
      generation !== meshGeneration
    )
      return;
    const loaded = performance.now();
    viewer.load(data, reset);
    viewer.renderNow();
    const rendered = performance.now();
    latestViewerTiming = { revision: fingerprint, load_ms: loaded - start, render_ms: rendered - loaded };
    renderTimings(current);
    meshKey = key;
    const shared = graph?.selection;
    if (!measurementEnabled && shared?.actor === "agent" && shared.feature_id === current.result?.features[index]?.id && shared.point) viewer.showSelection(shared.point);
    const isFinal = index === (current.result?.features.length || 0) - 1;
    $("model-stats").innerHTML =
      `<span><strong>${data.bounds.map((n) => Number(n.toFixed(1))).join(" × ")}</strong> mm</span><span><strong>${(data.volume / 1000).toFixed(2)}</strong> cm³</span>${!isFinal ? "<span>Earlier feature</span>" : ""}`;
    const traceId = current.trace_id;
    if (traceId && isFinal && !reportedTraces.has(traceId)) {
      reportedTraces.add(traceId);
      void api(`/traces/${encodeURIComponent(traceId)}/viewer`, {
        method: "POST",
        body: JSON.stringify({ revision: fingerprint, load_ms: loaded - start, render_ms: rendered - loaded }),
      }).catch(() => reportedTraces.delete(traceId));
    }
  } catch (error) {
    if (generation === meshGeneration) {
      renderedKey = "";
      toast(String(error), true);
    }
  }
}

async function syncFeatureGraph(state: State) {
  const key = `${state.id}:${state.fingerprint}`;
  if (graphKey === key) return;
  graphKey = key;
  const generation = ++graphGeneration;
  try {
    const next = await api<FeatureGraph>(`/designs/${state.id}/features`);
    if (generation !== graphGeneration) return;
    if (state.id !== currentId || next.revision !== current?.fingerprint) {
      if (graphKey === key) graphKey = "";
      return;
    }
    graph = next;
    const structured = graph.mode === "structured";
    $("model-mode").textContent = structured ? "Features" : "Python";
    $("editor-note").textContent = structured
      ? "Select a feature to edit. Human and agent changes share this history."
      : "Custom CadQuery model. Edit named dimensions here or ask your agent to edit the source.";
    $<HTMLButtonElement>("add-feature").disabled = !structured || featureSaving;
    $("feature-actions").hidden = !structured;
    if (structured) {
      if (!graph.features.some((feature) => feature.id === selectedDefinition)) {
        selectedDefinition = graph.features.at(-1)?.id || "";
      }
      renderStructuredFeatures();
      applySharedSelection(graph.selection);
    } else {
      renderFeatures();
    }
  } catch (error) {
    if (graphKey === key) graphKey = "";
    $("editor-note").textContent = `Feature editor unavailable: ${String(error)}`;
  }
}

function renderStructuredFeatures() {
  if (!graph || graph.mode !== "structured") return;
  $("feature-count").textContent = String(graph.features.length);
  const bodies = current?.result?.stats?.solids || 0;
  $("solid-count").textContent = `${bodies} solid ${bodies === 1 ? "body" : "bodies"} · ${graph.features.length} features`;
  $("feature-list").innerHTML = graph.features.length
    ? graph.features.map((feature) => `<button class="feature-row ${feature.id === selectedDefinition ? "active" : ""} ${feature.suppressed ? "suppressed" : ""}" data-definition="${escape(feature.id)}" aria-pressed="${feature.id === selectedDefinition}" title="${escape(feature.id)}${feature.suppressed ? " · suppressed" : ""}">${icon(feature.type === "profile" ? "grid-2x2" : operationIcons[feature.type] || "component")}<span>${escape(feature.name || feature.id)}<em>${escape(feature.type)}${feature.suppressed ? " · suppressed" : ""}</em></span></button>`).join("")
    : '<div class="empty-state">Your first feature will appear here.</div>';
  const index = graph.features.findIndex((feature) => feature.id === selectedDefinition);
  const selected = graph.features[index];
  $("feature-actions").hidden = !selected;
  $<HTMLButtonElement>("edit-feature").disabled = featureSaving;
  $<HTMLButtonElement>("move-feature-up").disabled = index <= 0 || featureSaving;
  $<HTMLButtonElement>("move-feature-down").disabled = index < 0 || index === graph.features.length - 1 || featureSaving;
  $<HTMLButtonElement>("suppress-feature").disabled = featureSaving;
  $<HTMLButtonElement>("delete-feature").disabled = featureSaving;
  $("suppress-feature").textContent = selected?.suppressed ? "Unsuppress" : "Suppress";
  refreshIcons();
}

function selectDefinition(id: string, publish: boolean) {
  const feature = graph?.features.find((item) => item.id === id);
  if (!feature) return;
  selectedDefinition = id;
  renderStructuredFeatures();
  const index = current?.result?.features.findIndex((item) => item.id === id) ?? -1;
  if (index >= 0 && current?.status === "ready") {
    selectedFeature = index;
    void loadMesh(index, false);
  }
  $("selection-readout").hidden = false;
  $("selection-readout").textContent = feature.type === "profile"
    ? `${feature.name || id} · profile. Edit to view its dimensions.`
    : `${feature.name || id}${feature.suppressed ? " · suppressed" : " · click a surface to share a point"}`;
  if (publish) void publishSelection(id);
}

async function publishSelection(featureId: string, pick?: SurfacePick) {
  if (!current || graph?.mode !== "structured") return;
  const id = currentId;
  const revision = current.fingerprint;
  try {
    const selection = await api<SharedSelection>(`/designs/${id}/selection`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: revision, feature_id: featureId, point: pick?.point, normal: pick?.normal, actor: "human" }),
    });
    if (id === currentId && current?.fingerprint === revision) {
      sharedSelectionKey = JSON.stringify(selection);
      $("collaboration-status").textContent = "Your selection is available to the agent.";
    }
  } catch (error) {
    toast(`Selection was not shared: ${String(error)}`, true);
  }
}

function applySharedSelection(selection?: SharedSelection | null) {
  if (!selection || selection.revision !== current?.fingerprint) return;
  const key = JSON.stringify(selection);
  if (sharedSelectionKey === key) return;
  if (selection.actor !== "agent") { sharedSelectionKey = key; return; }
  // Leave the key unapplied while the human is busy; render() retries the saved selection.
  if (modal.open || dirty || measurementEnabled) return;
  sharedSelectionKey = key;
  if (selection.feature_id) selectDefinition(selection.feature_id, false);
  viewer?.showSelection(selection.point || undefined);
  $("collaboration-status").textContent = "Agent selection shown in the feature tree.";
  if (selection.point) {
    $("selection-readout").hidden = false;
    $("selection-readout").textContent = `Agent selected ${selection.point.map((value) => value.toFixed(2)).join(", ")} mm`;
  }
}

if (viewer) viewer.onPick = (pick) => {
  $("selection-readout").hidden = false;
  $("selection-readout").textContent = measurementEnabled
    ? pick.distance === undefined
      ? "First point selected. Click a second surface point."
      : `Point-to-point distance: ${pick.distance.toFixed(2)} mm · mesh approximation`
    : `Surface point: ${pick.point.map((value) => value.toFixed(2)).join(", ")} mm`;
  const snapshotId = current?.result?.features[selectedFeature]?.id;
  if (snapshotId) void publishSelection(snapshotId, pick);
};

$("measure-button").onclick = () => {
  measurementEnabled = !measurementEnabled;
  viewer?.setMeasurement(measurementEnabled);
  $("measure-button").classList.toggle("active", measurementEnabled);
  $("measure-button").setAttribute("aria-pressed", String(measurementEnabled));
  $("selection-readout").hidden = !measurementEnabled;
  $("selection-readout").textContent = "Click two surface points to measure a straight-line distance.";
};

const featureNames: Record<FeatureType, string> = { profile: "Profile", extrude: "Extrude", fillet: "Fillet", shell: "Shell", boolean: "Boolean" };
const dimensionText = (value: unknown) => typeof value === "object" && value !== null && "parameter" in value
  ? `@${String((value as { parameter: string }).parameter)}` : String(value ?? "");
function dimensionValue(value: string): number | { parameter: string } {
  const text = value.trim();
  if (/^@[A-Za-z_][\w-]*$/.test(text)) return { parameter: text.slice(1) };
  const number = Number(text);
  if (!text || !Number.isFinite(number)) throw new Error("Enter a finite dimension in mm, or @ followed by a named parameter.");
  return number;
}
const dimensionField = (label: string, name: string, value: unknown) => `<label>${escape(label)}<input name="${name}" value="${escape(dimensionText(value))}" required autocomplete="off" spellcheck="false"></label>`;
const selectField = (label: string, name: string, values: [string, string][], selected: unknown) => `<label>${escape(label)}<select name="${name}" aria-label="${escape(label)}">${values.map(([value, title]) => `<option value="${escape(value)}" ${value === selected ? "selected" : ""}>${escape(title)}</option>`).join("")}</select></label>`;

function featureFields(type: FeatureType, params: Record<string, unknown>, features: Definition[], editingId?: string) {
  const editingIndex = editingId ? features.findIndex((feature) => feature.id === editingId) : features.length;
  const candidates = features.slice(0, editingIndex < 0 ? features.length : editingIndex).filter((feature) => !feature.suppressed);
  const profiles = candidates.filter((feature) => feature.type === "profile");
  const solids = candidates.filter((feature) => feature.type !== "profile");
  const options = (items: Definition[]) => items.map((feature): [string, string] => [feature.id, feature.name || feature.id]);
  const target = () => selectField("Target feature", "target", options(solids), params.target || solids.at(-1)?.id);
  if (type === "profile") {
    const origin = (params.origin || [0, 0, 0]) as unknown[];
    return `${selectField("Profile shape", "shape", [["rectangle", "Rectangle"], ["circle", "Circle"], ["polygon", "Polygon"]], params.shape || "rectangle")}${selectField("Sketch plane", "plane", [["XY", "XY · top"], ["XZ", "XZ · front"], ["YZ", "YZ · right"]], params.plane || "XY")}
      <div id="profile-dimensions" class="form-grid"></div><div class="form-grid three">${origin.map((value, index) => dimensionField(`Origin ${"XYZ"[index]} (mm)`, `origin-${index}`, value)).join("")}</div>
      <div id="profile-preview" class="profile-preview" aria-label="Profile dimension preview"></div>`;
  }
  if (type === "extrude") return `${selectField("Profile", "profile", options(profiles), params.profile || profiles.at(-1)?.id)}${dimensionField("Distance (mm)", "distance", params.distance ?? 10)}${selectField("Operation", "operation", [["new", "New body"], ["add", "Add to target"], ["cut", "Cut from target"], ["intersect", "Intersect target"]], params.operation || "new")}<div id="extrude-target">${target()}</div>`;
  if (type === "fillet") {
    const edges = (params.edges || { kind: "parallel", axis: "Z" }) as { kind: string; axis?: string };
    return `${target()}${dimensionField("Radius (mm)", "radius", params.radius ?? 2)}${selectField("Edges", "edges", [["all", "All edges"], ["X", "Parallel to X"], ["Y", "Parallel to Y"], ["Z", "Parallel to Z"]], edges.kind === "all" ? "all" : edges.axis)}`;
  }
  if (type === "shell") {
    const face = (params.open_faces || { kind: "extreme", axis: "Z", side: "max" }) as { axis: string; side: string };
    return `${target()}${dimensionField("Wall thickness (mm)", "thickness", params.thickness ?? 2)}${selectField("Open face axis", "axis", [["X", "X"], ["Y", "Y"], ["Z", "Z"]], face.axis)}${selectField("Open face side", "side", [["max", "Maximum · positive side"], ["min", "Minimum · negative side"]], face.side)}`;
  }
  const chosenTools = (params.tools || []) as string[];
  return `${target()}${selectField("Operation", "operation", [["union", "Union"], ["cut", "Cut"], ["intersect", "Intersect"]], params.operation || "union")}<fieldset class="tool-choices"><legend>Tool features</legend>${solids.map((feature) => `<label><input type="checkbox" name="tool" value="${escape(feature.id)}" ${chosenTools.includes(feature.id) ? "checked" : ""}>${escape(feature.name || feature.id)}</label>`).join("") || "Create another solid to use as a tool."}</fieldset>`;
}

function profileDimensions(shape: string, params: Record<string, unknown>) {
  $("profile-dimensions").innerHTML = shape === "circle"
    ? dimensionField("Radius (mm)", "radius", params.radius ?? 15)
    : shape === "polygon"
      ? `<label class="span-all">Polygon points (mm, one x,y pair per line)<textarea name="points" rows="5" required spellcheck="false">${escape(((params.points || [[-20, -15], [20, -15], [20, 15], [-20, 15]]) as unknown[][]).map((point) => point.map(dimensionText).join(", ")).join("\n"))}</textarea></label>`
      : `${dimensionField("Width (mm)", "width", params.width ?? 40)}${dimensionField("Height (mm)", "height", params.height ?? 30)}`;
  updateProfilePreview();
}

function readFeatureParams(type: FeatureType, form: HTMLFormElement): Record<string, unknown> {
  const data = new FormData(form);
  const text = (name: string) => String(data.get(name) || "");
  const dimension = (name: string) => dimensionValue(text(name));
  if (type === "profile") {
    const shape = text("shape");
    return { shape, plane: text("plane"), origin: [dimension("origin-0"), dimension("origin-1"), dimension("origin-2")],
      ...(shape === "rectangle" ? { width: dimension("width"), height: dimension("height") }
        : shape === "circle" ? { radius: dimension("radius") }
          : { points: text("points").trim().split(/\n+/).map((line) => {
            const pair = line.split(",").map(dimensionValue);
            if (pair.length !== 2) throw new Error("Each polygon point needs exactly two coordinates, separated by a comma.");
            return pair;
          }) }) };
  }
  if (type === "extrude") return { profile: text("profile"), distance: dimension("distance"), operation: text("operation"), ...(text("operation") === "new" ? {} : { target: text("target") }) };
  if (type === "fillet") return { target: text("target"), radius: dimension("radius"), edges: text("edges") === "all" ? { kind: "all" } : { kind: "parallel", axis: text("edges") } };
  if (type === "shell") return { target: text("target"), thickness: dimension("thickness"), open_faces: { kind: "extreme", axis: text("axis"), side: text("side") } };
  const tools = data.getAll("tool").map(String);
  if (!tools.length) throw new Error("Choose at least one tool feature.");
  return { target: text("target"), tools, operation: text("operation") };
}

function updateProfilePreview() {
  const preview = $("profile-preview");
  if (!preview) return;
  try {
    const params = readFeatureParams("profile", $<HTMLFormElement>("feature-form"));
    const number = (value: unknown): number => {
      if (typeof value === "number") return value;
      const ref = value as { parameter: string };
      const entry = graph?.parameters[ref.parameter];
      if (!entry) throw new Error(`Unknown parameter: ${ref.parameter}`);
      return typeof entry === "number" ? entry : entry.value;
    };
    let points: number[][];
    if (params.shape === "circle") {
      const radius = number(params.radius);
      if (radius <= 0) throw new Error("Radius must be positive.");
      points = Array.from({ length: 65 }, (_, index) => [Math.cos(index / 64 * Math.PI * 2) * radius, Math.sin(index / 64 * Math.PI * 2) * radius]);
    } else if (params.shape === "rectangle") {
      const halfWidth = number(params.width) / 2, halfHeight = number(params.height) / 2;
      if (halfWidth <= 0 || halfHeight <= 0) throw new Error("Width and height must be positive.");
      points = [[-halfWidth, -halfHeight], [halfWidth, -halfHeight], [halfWidth, halfHeight], [-halfWidth, halfHeight]];
    } else points = (params.points as unknown[][]).map((point) => point.map(number));
    if (points.length < 3) throw new Error("A polygon needs at least three points.");
    const xs = points.map((point) => point[0]), ys = points.map((point) => point[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const scale = Math.max(maxX - minX, maxY - minY, 1) / 140;
    const centerX = (maxX + minX) / 2, centerY = (maxY + minY) / 2;
    const mapped = points.map(([x, y]) => `${150 + (x - centerX) / scale},${90 - (y - centerY) / scale}`).join(" ");
    preview.innerHTML = `<svg viewBox="0 0 300 180" role="img" aria-label="${escape(params.shape)} profile on ${escape(params.plane)} plane"><path d="M0 90H300M150 0V180" stroke="#d7e4e1" stroke-dasharray="3 4"/><polygon points="${mapped}" fill="#daeee8" stroke="#19867a" stroke-width="2"/></svg><span>${(maxX - minX).toFixed(2)} × ${(maxY - minY).toFixed(2)} mm · ${escape(params.plane)} plane · dimensional preview</span>`;
  } catch (error) {
    preview.textContent = String(error).replace(/^Error: /, "");
  }
}

async function submitOperations(operations: unknown[], revision: string, label: string): Promise<VerifiedEdit> {
  if (!current || featureSaving) throw new Error("Wait for the current edit to finish.");
  const id = currentId;
  featureSaving = true;
  $<HTMLButtonElement>("add-feature").disabled = true;
  renderStructuredFeatures();
  $("collaboration-status").textContent = "Validating this edit…";
  let committed: VerifiedEdit | null = null;
  try {
    const dispatched = performance.now();
    const result = await api<VerifiedEdit>(`/designs/${id}/operations`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: revision, operations, actor: "human", label, trace_id: crypto.randomUUID() }),
    });
    committed = result;
    latestEditTiming = { revision: result.revision, api_ms: performance.now() - dispatched };
    if (id === currentId) {
      graphKey = "";
      const state = await api<State>(`/designs/${id}`);
      render(state);
      await syncFeatureGraph(state);
      $("collaboration-status").textContent = `Saved and verified · ${result.body_count} ${result.body_count === 1 ? "body" : "bodies"}`;
    }
    return result;
  } catch (error) {
    if (committed) {
      // A failed read after commit cannot turn a successful edit into a failed write.
      // Close the form normally and let polling retry only the preview/status reads.
      if (id === currentId) {
        graphKey = "";
        renderedKey = "";
        $("agent-revision").textContent = committed.revision.slice(0, 12);
        $("agent-revision").title = committed.revision;
        $("collaboration-status").textContent = "Saved and verified. Waiting to refresh the preview…";
      }
      return committed;
    }
    $("collaboration-status").textContent = error instanceof ApiError && [400, 409, 422].includes(error.status)
      ? "Edit was not applied. Your last saved model is unchanged."
      : "Edit outcome could not be confirmed. Refresh the saved model before trying again.";
    throw error;
  } finally {
    featureSaving = false;
    $<HTMLButtonElement>("add-feature").disabled = graph?.mode !== "structured";
    renderStructuredFeatures();
  }
}

function renderTimings(state: State) {
  const timing = state.timings || {};
  const total = timing.total_seconds;
  $("latency-details").hidden = typeof total !== "number";
  if (typeof total !== "number") return;
  const milliseconds = (seconds: unknown) => typeof seconds === "number" ? `${(seconds * 1000).toFixed(0)} ms` : "—";
  $("latency-summary").textContent = `Build ${milliseconds(total)}${timing.worker_reused ? " · warm worker" : ""}`;
  const rows: [string, string][] = [];
  if (latestEditTiming?.revision === state.fingerprint) rows.push(["API round trip", `${latestEditTiming.api_ms.toFixed(0)} ms`]);
  rows.push(["CAD geometry", milliseconds(timing.kernel_seconds)]);
  rows.push(["Solid checks", milliseconds(timing.validation_seconds)]);
  rows.push(["Preview mesh", milliseconds(timing.mesh_seconds)]);
  if (typeof timing.import_seconds === "number" && timing.import_seconds > 0) rows.push(["CAD startup", milliseconds(timing.import_seconds)]);
  if (latestViewerTiming?.revision === state.fingerprint) {
    rows.push(["Viewer mesh fetch", `${latestViewerTiming.load_ms.toFixed(0)} ms`]);
    rows.push(["Viewer draw", `${latestViewerTiming.render_ms.toFixed(0)} ms`]);
  }
  $("latency-values").innerHTML = rows.map(([name, value]) => `<dt>${escape(name)}</dt><dd>${escape(value)}</dd>`).join("");
}

function showFeatureForm(type: FeatureType, existing?: Definition) {
  if (!graph || graph.mode !== "structured" || !current) return;
  const revision = graph.revision;
  const params = existing?.params || {};
  const title = `${existing ? "Edit" : "Add"} ${featureNames[type].toLowerCase()}`;
  const hint = capabilities?.operations[type];
  showModal(title, `<p class="modal-copy">${escape(hint?.description || "Create editable geometry with exact dimensions.")} Dimensions use millimeters. Enter <code>@width</code> to bind a named parameter.</p>
    <form id="feature-form" class="modal-form" data-revision="${escape(revision)}"><label>Feature name<input name="name" value="${escape(existing?.name || featureNames[type])}" maxlength="100" required></label>
    ${featureFields(type, params, graph.features, existing?.id)}
    <p id="feature-conflict" class="conflict-note" hidden>The design changed while this form was open. Your entries are preserved. Reopen the feature to review the new revision before applying.</p>
    <p id="feature-form-error" class="conflict-note" role="alert" hidden></p>
    ${hint?.constraints?.length ? `<details class="feature-constraints"><summary>Operation constraints</summary><ul>${hint.constraints.map((constraint) => `<li>${escape(constraint)}</li>`).join("")}</ul></details>` : ""}
    <div class="form-actions"><span>One saved edit · validated before applying</span><button class="button primary" type="submit">${icon("check")}Apply feature</button></div></form>`);
  const form = $<HTMLFormElement>("feature-form");
  if (type === "profile") {
    profileDimensions(String(params.shape || "rectangle"), params);
    form.querySelector<HTMLSelectElement>('[name="shape"]')!.onchange = (event) => profileDimensions((event.target as HTMLSelectElement).value, params);
    form.addEventListener("input", updateProfilePreview);
    form.addEventListener("change", updateProfilePreview);
  }
  if (type === "extrude") {
    const updateTarget = () => { $("extrude-target").hidden = form.querySelector<HTMLSelectElement>('[name="operation"]')!.value === "new"; };
    form.querySelector<HTMLSelectElement>('[name="operation"]')!.onchange = updateTarget;
    updateTarget();
  }
  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = form.querySelector<HTMLButtonElement>('[type="submit"]')!;
    button.disabled = true;
    $("feature-form-error").hidden = true;
    try {
      if (current?.fingerprint !== revision) throw new Error("The design has a newer revision. Your entries were not applied; close and reopen this feature to review the latest values.");
      const name = String(new FormData(form).get("name"));
      const nextParams = readFeatureParams(type, form);
      const id = existing?.id || `${type}_${crypto.randomUUID().slice(0, 8)}`;
      await submitOperations([existing
        ? { op: "update", id, changes: { name, params: nextParams, replace_params: true } }
        : { op: "add", feature: { id, type, name, params: nextParams } }], revision, `${existing ? "Edit" : "Add"} ${name}`);
      selectedDefinition = id;
      renderStructuredFeatures();
      modal.close();
      toast(`${name} saved and verified.`);
    } catch (error) {
      $("feature-form-error").textContent = String(error).replace(/^Error: /, "");
      $("feature-form-error").hidden = false;
      button.disabled = false;
    }
  };
}

$("add-feature").onclick = () => {
  if (graph?.mode !== "structured") return;
  showModal("Add a feature", `<p class="modal-copy">Start with a profile, then create or modify solid geometry. Features keep the same IDs when you or your agent edit them.</p><div class="feature-type-grid">${Object.entries(featureNames).map(([type, name]) => `<button class="feature-type" data-add-type="${type}">${icon(type === "profile" ? "grid-2x2" : operationIcons[type] || "component")}<strong>${name}</strong><span>${escape(capabilities?.operations[type]?.description || { profile: "Rectangle, circle or polygon", extrude: "Give a profile depth", fillet: "Round selected edges", shell: "Hollow a solid", boolean: "Combine or subtract solids" }[type as FeatureType])}</span></button>`).join("")}</div>`);
  $("modal-content").onclick = (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-add-type]");
    if (button) showFeatureForm(button.dataset.addType as FeatureType);
  };
};
$("edit-feature").onclick = () => {
  const selected = graph?.features.find((feature) => feature.id === selectedDefinition);
  if (selected) showFeatureForm(selected.type, selected);
};
async function simpleFeatureEdit(operation: unknown, label: string) {
  if (!graph) return;
  try { await submitOperations([operation], graph.revision, label); toast(`${label}. Saved and verified.`); }
  catch (error) { toast(String(error), true); }
}
$("suppress-feature").onclick = () => {
  const feature = graph?.features.find((item) => item.id === selectedDefinition);
  if (feature) void simpleFeatureEdit({ op: "update", id: feature.id, changes: { suppressed: !feature.suppressed } }, `${feature.suppressed ? "Unsuppress" : "Suppress"} ${feature.name || feature.id}`);
};
for (const [button, delta] of [["move-feature-up", -1], ["move-feature-down", 1]] as const) $(button).onclick = () => {
  const index = graph?.features.findIndex((feature) => feature.id === selectedDefinition) ?? -1;
  if (index >= 0) void simpleFeatureEdit({ op: "move", id: selectedDefinition, index: index + delta }, "Reorder feature");
};
$("delete-feature").onclick = () => {
  const feature = graph?.features.find((item) => item.id === selectedDefinition);
  if (!feature || !graph) return;
  const revision = graph.revision;
  showModal("Delete feature", `<p class="modal-copy">Delete <strong>${escape(feature.name || feature.id)}</strong>? The edit is saved in history. If another feature depends on it, the app will reject the deletion and keep your model.</p><div class="modal-actions"><button class="button" id="cancel-feature-delete">Cancel</button><button class="button danger-text" id="confirm-feature-delete">Delete feature</button></div><p id="delete-error" class="conflict-note" role="alert" hidden></p>`);
  $("cancel-feature-delete").onclick = () => modal.close();
  $("confirm-feature-delete").onclick = async () => {
    $<HTMLButtonElement>("confirm-feature-delete").disabled = true;
    try { await submitOperations([{ op: "remove", id: feature.id }], revision, `Delete ${feature.name || feature.id}`); modal.close(); toast("Feature deleted. The previous model is in saved checkpoints."); }
    catch (error) { $("delete-error").textContent = String(error); $("delete-error").hidden = false; $<HTMLButtonElement>("confirm-feature-delete").disabled = false; }
  };
};

async function poll() {
  if (pollRunning || selecting) return;
  pollRunning = true;
  let id = currentId;
  try {
    const workspace = await api<{
      active_design: string | null;
      revision: number;
    }>("/workspace");
    if (
      !selecting &&
      workspace.active_design &&
      workspace.active_design !== currentId
    ) {
      if (!dirty && !applying && !featureSaving && !modal.open) {
        await loadDesigns();
        await selectDesign(workspace.active_design, false);
      } else if (dirty && selectionNotice !== workspace.revision) {
        selectionNotice = workspace.revision;
        toast(
          "Another design is ready. Apply or reload your pending dimensions to switch.",
        );
      }
    }
    id = currentId;
    if (!id) return;
    const state = await api<State>(`/designs/${id}`);
    if (currentId === id && !applying && !featureSaving) render(state);
    if (graph?.mode === "structured" && events?.readyState !== EventSource.OPEN && !modal.open && !dirty) {
      const selection = await api<SharedSelection>(`/designs/${id}/selection`);
      if (id === currentId) applySharedSelection(selection);
    }
  } catch (error) {
    if (id === currentId) {
      $("build-status").textContent = "Connection or source error";
      $("error-banner").hidden = false;
      $("error-details").textContent = String(error);
      // Do not offer a stale export after a malformed manifest or a server disconnect.
      if (current) current.status = "error";
    }
  } finally {
    pollRunning = false;
  }
}

$("parameter-fields").addEventListener("input", () => {
  dirty = true;
  $<HTMLButtonElement>("apply-button").disabled = applying;
  $("parameter-status").textContent =
    "Unapplied changes · Ctrl + Enter to apply";
  $("footer-status").textContent = "Unapplied parameter changes";
});
$("reset-parameters").onclick = () => {
  if (current) renderParameters(current);
};
$<HTMLFormElement>("parameter-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!current || !dirty || applying) return;
  const id = currentId;
  const values = Object.fromEntries(
    [...new FormData($<HTMLFormElement>("parameter-form"))].map(([k, v]) => [
      k,
      Number(v),
    ]),
  );
  applying = true;
  $<HTMLButtonElement>("apply-button").disabled = true;
  try {
    const state = await api<State>(`/designs/${id}/parameters`, {
      method: "PUT",
      body: JSON.stringify({ values, fingerprint: formFingerprint }),
    });
    if (id === currentId) {
      dirty = false;
      renderParameters(state);
      render(state);
    }
    toast("Parameters saved. Rebuilding the model.");
  } catch (error) {
    toast(String(error), true);
  } finally {
    applying = false;
    $<HTMLButtonElement>("apply-button").disabled = !dirty;
  }
};

$("new-design").onclick = async () => {
  try {
    const templates = await api<{ id: string; name: string }[]>("/templates");
    showModal(
      "Create a design",
      `<p class="modal-copy">Start with a blank canvas, a template, or a copy of your current design.</p><form id="new-form" class="modal-form"><label>Design name<input name="name" placeholder="My next design" required maxlength="100" autofocus></label><label>Starting point<select name="template"><option value="__blank" selected>Blank model · no geometry</option>${templates.map((t) => `<option value="${t.id}">${escape(t.name)}</option>`).join("")}${current ? `<option value="__copy">Copy ${escape(current.design.name)}</option>` : ""}</select></label><button class="button primary" type="submit">${icon("plus")}Create design</button></form>`,
    );
    $<HTMLFormElement>("new-form").onsubmit = async (event) => {
      event.preventDefault();
      const form = $<HTMLFormElement>("new-form"),
        data = new FormData(form);
      const button = form.querySelector("button")!;
      button.disabled = true;
      try {
        const template = String(data.get("template"));
        const result = await api<{ id: string }>("/designs", {
          method: "POST",
          body: JSON.stringify({
            name: data.get("name"),
            ...(template === "__copy"
              ? { copy_from: currentId }
              : { template: template === "__blank" ? null : template }),
          }),
        });
        modal.close();
        await loadDesigns();
        await selectDesign(result.id);
        toast("Design created and saved locally.");
      } catch (error) {
        toast(String(error), true);
        button.disabled = false;
      }
    };
  } catch (error) {
    toast(String(error), true);
  }
};

$("save-checkpoint").onclick = () => {
  if (!current) return;
  showModal(
    "Save a checkpoint",
    `<p class="modal-copy">Save the current source and dimensions so you can return to this design later.${dirty ? " Apply your pending parameter changes first if you want to include them." : ""}</p><form id="checkpoint-form" class="modal-form"><label>Checkpoint name<input name="label" value="${escape(current.design.name)} — revision" maxlength="160" required autofocus></label><button class="button primary" type="submit">${icon("bookmark-plus")}Save checkpoint</button></form>`,
  );
  $<HTMLFormElement>("checkpoint-form").onsubmit = async (event) => {
    event.preventDefault();
    try {
      await api(`/designs/${currentId}/history`, {
        method: "POST",
        body: JSON.stringify({
          label: new FormData($<HTMLFormElement>("checkpoint-form")).get(
            "label",
          ),
        }),
      });
      modal.close();
      toast("Checkpoint saved, including editable source and dimensions.");
    } catch (error) {
      toast(String(error), true);
    }
  };
};

$("history-button").onclick = async () => {
  if (!current) return;
  try {
    const checkpoints = await api<
      { id: string; label: string; created_at: string }[]
    >(`/designs/${currentId}/history`);
    showModal(
      "Saved checkpoints",
      `<p class="modal-copy">Restore both the model source and parameters. Your current saved design is checkpointed before a restore.</p>${checkpoints.length ? checkpoints.map((c) => `<div class="checkpoint"><div><strong>${escape(c.label)}</strong><small>${new Date(c.created_at).toLocaleString()}</small></div><button class="button" data-restore="${c.id}">Restore</button></div>`).join("") : '<p class="empty-state">No checkpoints yet. Save one from the toolbar, or apply a parameter change to create an automatic checkpoint.</p>'}`,
    );
    $("modal-content").onclick = async (event) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>(
        "[data-restore]",
      );
      if (!button || !current) return;
      if (
        dirty &&
        !window.confirm(
          "Discard unapplied parameters and restore this checkpoint?",
        )
      )
        return;
      button.disabled = true;
      try {
        const state = await api<State>(
          `/designs/${currentId}/history/${button.dataset.restore}/restore`,
          {
            method: "POST",
            body: JSON.stringify({ fingerprint: current.fingerprint }),
          },
        );
        dirty = false;
        renderParameters(state);
        render(state);
        modal.close();
        toast("Checkpoint restored. Rebuilding the model.");
      } catch (error) {
        button.disabled = false;
        toast(String(error), true);
      }
    };
  } catch (error) {
    toast(String(error), true);
  }
};

$("source-button").onclick = async () => {
  if (!current) return;
  try {
    const source = await api<{ source: string; design?: State["design"] }>(
      `/designs/${currentId}/source`,
    );
    const structured = source.design?.features != null;
    const text = structured ? JSON.stringify({ features: source.design!.features, parameters: source.design!.parameters }, null, 2) : source.source;
    const location = structured ? current.source_path.replace(/model\.py$/, "design.json") : current.source_path;
    showModal(
      structured ? "Editable feature definitions" : "Editable design source",
      `<p class="modal-copy">${structured ? "These saved feature definitions are shared by the editor and agent. Use the feature controls or revision-checked API to change them. Custom CadQuery remains available through a deliberate source edit." : "Codex edits this Python file and its neighboring <code>design.json</code>. Saved edits rebuild automatically while the viewer is open."}</p><p class="source-location">${escape(location)}</p><pre class="source-block">${escape(text)}</pre><div class="modal-actions"><button class="button" id="copy-source">${icon("copy")}Copy source</button></div>`,
    );
    $("copy-source").onclick = async () => {
      try {
        await navigator.clipboard.writeText(text);
        toast("Source copied.");
      } catch {
        toast("Clipboard unavailable. Select and copy the source text.", true);
      }
    };
  } catch (error) {
    toast(String(error), true);
  }
};

function showAgentInstructions(text: string) {
  showModal("Connect an AI agent",
    `<p class="modal-copy">Paste these instructions into an agent that can access this computer. They identify this saved model and explain how to edit it.</p><textarea class="agent-context-text" id="agent-context-text" aria-label="AI connection instructions" readonly></textarea><div class="modal-actions"><button class="button" id="copy-agent-text">${icon("copy")}Copy instructions</button></div>`);
  $<HTMLTextAreaElement>("agent-context-text").value = text;
  $("copy-agent-text").onclick = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast("AI connection instructions copied.");
    } catch {
      const field = $<HTMLTextAreaElement>("agent-context-text");
      field.focus();
      field.select();
      toast("Use Ctrl+C to copy the selected instructions.");
    }
  };
}

async function agentInstructions(copy: boolean) {
  if (!current) return;
  const id = currentId;
  try {
    const context = await api<{ instructions: string }>(`/designs/${id}/agent-context`);
    if (id !== currentId) {
      toast("The selected model changed. Copy its instructions again.");
      return;
    }
    const text = context.instructions + (dirty ? "\nThe GUI has unapplied dimension edits. Coordinate with the user before overwriting those changes." : "");
    if (copy) {
      try {
        await navigator.clipboard.writeText(text);
        toast("AI connection instructions copied.");
        return;
      } catch {
        // Keep the full text available for manual copying when clipboard access is denied.
      }
    }
    showAgentInstructions(text);
  } catch (error) {
    toast(String(error), true);
  }
}
$("copy-agent-context").onclick = () => void agentInstructions(true);
$("view-agent-context").onclick = () => void agentInstructions(false);

$("export-button").onclick = () => {
  if (!current) return;
  const ready = current.status === "ready" && !current.result?.empty;
  showModal(
    "Export your design",
    `<p class="modal-copy">STL and STEP export the final feature of the saved design.${dirty ? " Apply pending parameter changes first to include them." : ""}${current.result?.empty ? " Add a solid to enable geometry exports. Your blank design can already be saved as a source archive." : !ready ? " Geometry exports are available after a successful build." : ""}</p>
    <button class="export-option" data-export="stl" ${ready ? "" : "disabled"}>${icon("package")}<span><strong>STL · 3D print mesh</strong><small>For your slicer. Dimensions are in millimeters.</small></span>${icon("download")}</button>
    <button class="export-option" data-export="step" ${ready ? "" : "disabled"}>${icon("box")}<span><strong>STEP · CAD solid</strong><small>Precise geometry for other CAD applications.</small></span>${icon("download")}</button>
    <button class="export-option" data-export="source">${icon("file-archive")}<span><strong>Design archive · editable source</strong><small>Python model, parameters, and saved checkpoints.</small></span>${icon("download")}</button>`,
  );
  $("modal-content").onclick = async (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>(
      "[data-export]",
    );
    if (!button || !current) return;
    button.disabled = true;
    const format = button.dataset.export!,
      id = currentId;
    try {
      const response = await fetch(
        `/api/designs/${id}/export/${format}?fingerprint=${current.fingerprint}`,
      );
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `${id}${format === "source" ? "-source.zip" : "." + format}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 30000);
      modal.close();
      toast("Export ready. Your editable design stays in the workspace.");
    } catch (error) {
      toast(String(error), true);
      button.disabled = false;
    }
  };
};

$("guide-button").onclick = () =>
  showModal(
    "A familiar CAD vocabulary",
    `<p class="modal-copy">Use Add feature to sketch a dimensional profile, extrude it, round edges, shell a body, or combine solids. Your agent uses the same feature definitions and revision checks. Existing Python models also remain editable through their named dimensions and source.</p><table class="guide-table"><tbody>
  <tr><td>Sketch & extrude</td><td>“Start with an 80 by 50 rectangle, 6 mm thick.”<br><code>Workplane → rect / circle / polyline → extrude</code></td></tr>
  <tr><td>Holes & pockets</td><td>“Add four 5 mm mounting holes, 10 mm from the edges.”<br><code>hole · cutBlind · cutThruAll</code></td></tr>
  <tr><td>Fillet & chamfer</td><td>“Round the vertical corners to 6 mm.”<br><code>edges → fillet / chamfer</code></td></tr>
  <tr><td>Shell</td><td>“Hollow it out with 2.4 mm walls and an open top.”<br><code>faces → shell</code></td></tr>
  <tr><td>Revolve, loft, sweep</td><td>Turn a profile, blend sections, or follow a path.<br><code>revolve · loft · sweep</code></td></tr>
  <tr><td>Patterns & booleans</td><td>“Repeat that hole six times around the center.”<br><code>rarray · polarArray · union · cut · intersect · mirror</code></td></tr>
  </tbody></table><p class="modal-copy" style="margin-top:18px">Select a feature to edit, suppress, reorder or delete it. Click a solid to share a surface point with your agent. The ruler measures an approximate straight-line distance between two mesh points. Operation forms use semantic face and edge directions. Full constrained sketches, exact topology picking, assembly mates and FeatureScript compatibility are not implemented. Advanced operations remain available through custom CadQuery source.</p>`,
  );

document.querySelectorAll<HTMLButtonElement>("[data-view]").forEach(
  (button) =>
    (button.onclick = () => {
      viewer?.setView(button.dataset.view!);
      document
        .querySelectorAll("[data-view]")
        .forEach((b) => b.classList.toggle("active", b === button));
    }),
);
$("fit-button").onclick = () => viewer?.fit();
$("edges-button").onclick = () =>
  $("edges-button").classList.toggle("active", viewer?.toggleEdges() ?? false);
$("grid-button").onclick = () =>
  $("grid-button").classList.toggle("active", viewer?.toggleGrid() ?? false);
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key === "Enter" && !modal.open) {
    $<HTMLFormElement>("parameter-form").requestSubmit();
    return;
  }
  if (
    modal.open ||
    ["INPUT", "SELECT", "TEXTAREA"].includes(
      (event.target as HTMLElement).tagName,
    )
  )
    return;
  if (event.key.toLowerCase() === "f") viewer?.fit();
  if (event.key.toLowerCase() === "g") $("grid-button").click();
  if (event.key.toLowerCase() === "e") $("edges-button").click();
  const modes: Record<string, string> = {
    "1": "iso",
    "2": "top",
    "3": "front",
    "4": "right",
  };
  if (modes[event.key])
    document
      .querySelector<HTMLButtonElement>(`[data-view="${modes[event.key]}"]`)
      ?.click();
});
window.addEventListener("beforeunload", (event) => {
  if (dirty) event.preventDefault();
});
refreshIcons();
async function initialize() {
  void api<Capabilities>("/capabilities").then((value) => { capabilities = value; }).catch(() => {});
  const workspace = await api<{ active_design: string | null }>("/workspace");
  if (workspace.active_design) currentId = workspace.active_design;
  await loadDesigns();
  if (!workspace.active_design && currentId) {
    await api("/workspace/active", {
      method: "PUT",
      body: JSON.stringify({ design_id: currentId }),
    });
  }
  await poll();
  events = new EventSource("/api/events");
  events.addEventListener("change", (event) => {
    try {
      const change = JSON.parse((event as MessageEvent).data) as { design_id?: string };
      if (!change.design_id || change.design_id === currentId) graphKey = "";
      void poll();
    } catch { /* Periodic polling remains available when an event is interrupted. */ }
  });
}
initialize().catch((error) => toast(String(error), true));
window.setInterval(() => void poll(), 1000);
