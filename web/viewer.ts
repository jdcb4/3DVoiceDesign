import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

export interface MeshData {
  positions: number[];
  indices: number[];
  edges: number[];
  bounds: number[];
  volume: number;
  solids: number;
}

export interface SurfacePick {
  point: [number, number, number];
  normal: [number, number, number];
  distance?: number;
}

export class Viewer {
  private renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  private scene = new THREE.Scene();
  private camera = new THREE.OrthographicCamera(-80, 80, 60, -60, 0.1, 20000);
  private controls: OrbitControls;
  private model = new THREE.Group();
  private grid = new THREE.GridHelper(400, 40, 0xc5ced2, 0xdce2e5);
  private center = new THREE.Vector3(0, 0, 0);
  private size = 100;
  private edgeLines: THREE.LineSegments | null = null;
  private edgesVisible = true;
  private span = 150;
  private mode = "iso";
  private hasModel = false;
  private annotations = new THREE.Group();
  private measurement = false;
  private firstPoint: THREE.Vector3 | null = null;
  private pointerStart = new THREE.Vector2();
  private renderFrame: number | null = null;
  private requestRender = () => {
    if (this.renderFrame !== null) return;
    this.renderFrame = window.requestAnimationFrame(() => {
      this.renderFrame = null;
      this.renderNow();
    });
  };
  onPick: ((pick: SurfacePick) => void) | null = null;

  constructor(private container: HTMLElement) {
    THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
    this.camera.up.set(0, 0, 1);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0xf2f5f6, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.container.prepend(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.1;
    this.controls.screenSpacePanning = true;
    // OrbitControls emits change while moving and while damping settles. Idle
    // CAD scenes must not continuously consume the CPU on software WebGL hosts.
    this.controls.addEventListener("change", this.requestRender);
    this.renderer.domElement.addEventListener("webglcontextrestored", this.requestRender);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x778a99, 2.6));
    const key = new THREE.DirectionalLight(0xffffff, 3.2);
    key.position.set(-70, -90, 180);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xc4e8eb, 1.3);
    fill.position.set(120, 70, 40);
    this.scene.add(fill);
    this.grid.rotation.x = Math.PI / 2;
    this.grid.position.z = -0.06;
    const gridMaterial = this.grid.material as THREE.LineBasicMaterial;
    gridMaterial.transparent = true;
    gridMaterial.opacity = 0.7;
    this.scene.add(this.grid, this.model, this.annotations);
    this.renderer.domElement.addEventListener("pointerdown", (event) => {
      this.pointerStart.set(event.clientX, event.clientY);
    });
    this.renderer.domElement.addEventListener("pointerup", (event) => {
      if (event.button !== 0 || this.pointerStart.distanceTo(new THREE.Vector2(event.clientX, event.clientY)) > 4) return;
      const rect = this.renderer.domElement.getBoundingClientRect();
      const raycaster = new THREE.Raycaster();
      raycaster.setFromCamera(new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      ), this.camera);
      const hit = raycaster.intersectObjects(this.model.children).find((intersection) => intersection.object instanceof THREE.Mesh);
      if (!hit?.face) return;
      const normal = hit.face.normal.clone().transformDirection(hit.object.matrixWorld);
      const pick: SurfacePick = {
        point: hit.point.toArray() as SurfacePick["point"],
        normal: normal.toArray() as SurfacePick["normal"],
      };
      if (this.measurement && this.firstPoint) {
        pick.distance = this.firstPoint.distanceTo(hit.point);
        this.addMarker(hit.point);
        this.annotations.add(new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([this.firstPoint, hit.point]),
          new THREE.LineBasicMaterial({ color: 0xd07826, depthTest: false }),
        ));
        this.firstPoint = null;
      } else {
        this.clearAnnotations();
        this.addMarker(hit.point);
        if (this.measurement) this.firstPoint = hit.point.clone();
      }
      this.onPick?.(pick);
    });
    this.setView("iso");
    new ResizeObserver(() => this.resize()).observe(container);
  }

  private resize() {
    const width = this.container.clientWidth,
      height = this.container.clientHeight;
    const aspect = width / Math.max(1, height);
    this.camera.left = (-this.span * aspect) / 2;
    this.camera.right = (this.span * aspect) / 2;
    this.camera.top = this.span / 2;
    this.camera.bottom = -this.span / 2;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
    this.requestRender();
  }

  load(data: MeshData, reset: boolean) {
    this.disposeModel();
    this.clearAnnotations();
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(data.positions, 3),
    );
    geometry.setIndex(data.indices);
    geometry.computeVertexNormals();
    this.model.add(
      new THREE.Mesh(
        geometry,
        new THREE.MeshStandardMaterial({
          color: 0x51a8a4,
          roughness: 0.48,
          metalness: 0.14,
          polygonOffset: true,
          polygonOffsetFactor: 1,
          polygonOffsetUnits: 1,
        }),
      ),
    );
    const edgeGeometry = new THREE.BufferGeometry();
    edgeGeometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(data.edges, 3),
    );
    this.edgeLines = new THREE.LineSegments(
      edgeGeometry,
      new THREE.LineBasicMaterial({
        color: 0x245b60,
        transparent: true,
        opacity: 0.55,
      }),
    );
    this.edgeLines.visible = this.edgesVisible;
    this.model.add(this.edgeLines);
    const box = new THREE.Box3().setFromObject(this.model);
    box.getCenter(this.center);
    this.size = Math.max(...box.getSize(new THREE.Vector3()).toArray(), 1);
    this.grid.position.z = box.min.z - 0.08;
    const gridScale = Math.max(1, Math.ceil(this.size / 160));
    this.grid.scale.setScalar(gridScale);
    if (!this.hasModel || reset) this.fit();
    this.hasModel = true;
    this.requestRender();
  }

  /** Render synchronously so timing measures drawing, not an arbitrary animation wait. */
  renderNow() {
    if (this.renderFrame !== null) {
      window.cancelAnimationFrame(this.renderFrame);
      this.renderFrame = null;
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  private addMarker(point: THREE.Vector3) {
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(Math.max(this.size * 0.008, 0.35), 12, 8),
      new THREE.MeshBasicMaterial({ color: 0xd07826, depthTest: false }),
    );
    marker.position.copy(point);
    marker.renderOrder = 10;
    this.annotations.add(marker);
    this.requestRender();
  }

  private clearAnnotations() {
    for (const child of [...this.annotations.children]) {
      this.annotations.remove(child);
      if (child instanceof THREE.Mesh || child instanceof THREE.Line) {
        child.geometry.dispose();
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        materials.forEach((material) => material.dispose());
      }
    }
    this.firstPoint = null;
    this.requestRender();
  }

  setMeasurement(enabled: boolean) {
    this.measurement = enabled;
    this.clearAnnotations();
    this.renderer.domElement.style.cursor = enabled ? "crosshair" : "";
  }

  showSelection(point?: number[]) {
    this.clearAnnotations();
    if (point?.length === 3) this.addMarker(new THREE.Vector3(...point));
  }

  clear() {
    this.disposeModel();
    this.clearAnnotations();
    this.hasModel = false;
  }

  private disposeModel() {
    for (const child of [...this.model.children]) {
      this.model.remove(child);
      if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments) {
        child.geometry.dispose();
        const materials = Array.isArray(child.material)
          ? child.material
          : [child.material];
        materials.forEach((material) => material.dispose());
      }
    }
    this.edgeLines = null;
  }

  fit() {
    const aspect =
      this.container.clientWidth / Math.max(1, this.container.clientHeight);
    this.span = (this.size * 1.8) / Math.min(aspect, 1);
    this.camera.zoom = 1;
    this.setView(this.mode);
    this.resize();
  }

  setView(mode: string) {
    this.mode = mode;
    const directions: Record<string, number[]> = {
      iso: [1.2, -1.5, 1.15],
      top: [0, 0, 1],
      front: [0, -1, 0],
      right: [1, 0, 0],
    };
    const direction = new THREE.Vector3(
      ...(directions[mode] || directions.iso),
    ).normalize();
    this.camera.up.set(0, mode === "top" ? 1 : 0, mode === "top" ? 0 : 1);
    this.camera.position
      .copy(this.center)
      .addScaledVector(direction, Math.max(this.size * 5, 300));
    this.controls.target.copy(this.center);
    this.camera.lookAt(this.center);
    this.controls.update();
    this.requestRender();
  }

  toggleEdges() {
    this.edgesVisible = !this.edgesVisible;
    if (this.edgeLines) this.edgeLines.visible = this.edgesVisible;
    this.requestRender();
    return this.edgesVisible;
  }
  toggleGrid() {
    this.grid.visible = !this.grid.visible;
    this.requestRender();
    return this.grid.visible;
  }
}
