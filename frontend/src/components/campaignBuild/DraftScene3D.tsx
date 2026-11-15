/** WebGL scene for AI drafting and the independent review. Loaded lazily (three.js is already used by the research scenes, so no new
 *  library). Everything drawn is a function of DraftSceneModel, which comes only from recorded events: a tower exists because the backend
 *  stored that ad group, its height is its real keyword count, the stream between the Main AI and the structure flows only while a request
 *  is outstanding, and the reviewer's scan runs only while its review request is. Nothing advances on a timer. */
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { DraftSceneModel, PhaseState } from "./draftScene";

const COL = { bg: "#0b131b", grid: "#16222d", main: "#3fb5c4", chal: "#b48cc8", done: "#7fc8a9", run: "#3fb5c4", fail: "#ef6461", wait: "#2a3a47",
  skip: "#d9a441", tower: "#a9d2d8", towerWriting: "#3fb5c4", towerPlanned: "#33505f", ad: "#f2f3f1", concern: "#ef6461", high: "#ff4d4a" };
const stateCol = (s: PhaseState) => (s === "done" ? COL.done : s === "running" ? COL.run : s === "failed" ? COL.fail : s === "skipped" ? COL.skip : COL.wait);

type Props = { model: DraftSceneModel; live: boolean; reduced: boolean; onFail: () => void };

export default function DraftScene3D({ model, live, reduced, onFail }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const api = useRef<{ set: (m: DraftSceneModel, live: boolean) => void } | null>(null);

  useEffect(() => {
    const el = host.current!;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "low-power" }); } catch { onFail(); return; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, el.clientWidth < 640 ? 1.25 : 1.75));
    renderer.setClearColor(COL.bg);
    renderer.domElement.setAttribute("aria-hidden", "true");
    renderer.domElement.style.display = "block";
    renderer.domElement.style.touchAction = "pan-y";
    el.appendChild(renderer.domElement);
    const lost = (e: Event) => { e.preventDefault(); onFail(); };
    renderer.domElement.addEventListener("webglcontextlost", lost);

    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(COL.bg, 16, 40);
    const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 200);
    camera.position.set(0, 8.5, 18.5);
    const controls = new OrbitControls(camera, renderer.domElement);
    Object.assign(controls, { enableDamping: true, dampingFactor: 0.08, enablePan: false, enableZoom: false, minPolarAngle: 0.35, maxPolarAngle: 1.3 });
    controls.touches = { ONE: null as unknown as THREE.TOUCH, TWO: THREE.TOUCH.DOLLY_ROTATE };
    controls.target.set(0, 0.8, 0);

    scene.add(new THREE.HemisphereLight("#cfe3e6", "#071016", 0.9));
    const key = new THREE.DirectionalLight("#ffffff", 1.5); key.position.set(5, 11, 7); scene.add(key);
    const rim = new THREE.PointLight(COL.chal, 0, 14); rim.position.set(5.2, 2.2, -1); scene.add(rim);
    const coreLight = new THREE.PointLight(COL.main, 0, 14); coreLight.position.set(-5.2, 2.2, -1); scene.add(coreLight);

    // ground: concentric rings, a quiet floor for depth
    const pts: number[] = [];
    for (let k = 1; k <= 7; k++) { const r = k * 1.6; for (let i = 0; i < 120; i++) { const a = (i / 120) * Math.PI * 2, b = ((i + 1) / 120) * Math.PI * 2; pts.push(Math.cos(a) * r, 0, Math.sin(a) * r, Math.cos(b) * r, 0, Math.sin(b) * r); } }
    const gg = new THREE.BufferGeometry(); gg.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    scene.add(new THREE.LineSegments(gg, new THREE.LineBasicMaterial({ color: COL.grid })));

    const mat = (c: string, emissive = 0.25) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.35, metalness: 0.15, emissive: new THREE.Color(c), emissiveIntensity: emissive });
    // the two AIs: faceted cores on pedestals
    const coreGeo = new THREE.IcosahedronGeometry(0.85, 1);
    const mainCore = new THREE.Mesh(coreGeo, mat(COL.main, 0.35)); mainCore.position.set(-5.2, 1.6, -1); scene.add(mainCore);
    const chalCore = new THREE.Mesh(coreGeo, mat(COL.chal, 0.15)); chalCore.position.set(5.2, 1.6, -1); scene.add(chalCore);
    const haloGeo = new THREE.TorusGeometry(1.25, 0.03, 8, 96);
    const mainHalo = new THREE.Mesh(haloGeo, new THREE.MeshBasicMaterial({ color: COL.main, transparent: true, opacity: 0.5 })); mainHalo.position.copy(mainCore.position); mainHalo.rotation.x = Math.PI / 2; scene.add(mainHalo);
    const chalHalo = new THREE.Mesh(haloGeo, new THREE.MeshBasicMaterial({ color: COL.chal, transparent: true, opacity: 0.25 })); chalHalo.position.copy(chalCore.position); chalHalo.rotation.x = Math.PI / 2; scene.add(chalHalo);

    // phase markers: an arc of plinths in front, lit by real phase state
    const plinthGeo = new THREE.CylinderGeometry(0.32, 0.4, 0.22, 24);
    const phaseMeshes: THREE.Mesh[] = [];
    for (let i = 0; i < 5; i++) {
      const a = Math.PI * (0.78 - (i / 4) * 0.56);
      const m = new THREE.Mesh(plinthGeo, mat(COL.wait, 0.4)); m.position.set(Math.cos(a) * 6.4, 0.11, Math.sin(a) * 6.4 - 1.5); scene.add(m); phaseMeshes.push(m);
    }
    const reviewMeshes: THREE.Mesh[] = [];
    for (let i = 0; i < 4; i++) {
      const m = new THREE.Mesh(new THREE.OctahedronGeometry(0.22, 0), mat(COL.wait, 0.4));
      const a = (i / 4) * Math.PI * 2; m.position.set(5.2 + Math.cos(a) * 1.9, 0.5, -1 + Math.sin(a) * 1.9); scene.add(m); reviewMeshes.push(m);
    }

    // structure: one tower per stored ad group (height = keyword count), small slabs for its ads
    const structure = new THREE.Group(); structure.position.set(0, 0, -1); scene.add(structure);
    const towerGeo = new THREE.BoxGeometry(0.55, 1, 0.55);
    const slabGeo = new THREE.BoxGeometry(0.62, 0.05, 0.62);
    const clearGroup = (g: THREE.Group) => { g.traverse((o: any) => { if (o.material && o !== g) o.material.dispose?.(); }); g.clear(); };

    // token stream: particles along a curve from the Main AI to the structure, moving only while a request is outstanding
    const N = 90;
    const curve = new THREE.QuadraticBezierCurve3(new THREE.Vector3(-5.2, 1.6, -1), new THREE.Vector3(-2.6, 4.2, -1), new THREE.Vector3(0, 1.4, -1));
    const sGeo = new THREE.BufferGeometry(); const sPos = new Float32Array(N * 3); sGeo.setAttribute("position", new THREE.BufferAttribute(sPos, 3));
    const stream = new THREE.Points(sGeo, new THREE.PointsMaterial({ color: COL.main, size: 0.09, transparent: true, opacity: 0.9 })); scene.add(stream);
    const seeds = Array.from({ length: N }, (_, i) => i / N);
    // review scan: a cone of light from the reviewer onto the structure while its request is outstanding
    const scan = new THREE.Mesh(new THREE.ConeGeometry(1.9, 5.4, 40, 1, true), new THREE.MeshBasicMaterial({ color: COL.chal, transparent: true, opacity: 0.0, side: THREE.DoubleSide, depthWrite: false }));
    scan.position.set(2.6, 1.6, -1); scan.rotation.z = Math.PI / 2; scene.add(scan);
    // concerns: shards orbiting the reviewer, one per objection the review returned
    const concerns = new THREE.Group(); concerns.position.copy(chalCore.position); scene.add(concerns);
    const verdictRing = new THREE.Mesh(new THREE.TorusGeometry(1.55, 0.05, 8, 96), new THREE.MeshBasicMaterial({ color: COL.wait, transparent: true, opacity: 0 }));
    verdictRing.position.copy(chalCore.position); verdictRing.rotation.x = Math.PI / 2; scene.add(verdictRing);

    let cur: DraftSceneModel = model, isLive = live;
    const set = (m: DraftSceneModel, l: boolean) => {
      cur = m; isLive = l;
      m.phases.forEach((p, i) => { const mm = phaseMeshes[i].material as THREE.MeshStandardMaterial; mm.color.set(stateCol(p.state)); mm.emissive.set(stateCol(p.state)); });
      m.challenger.phases.forEach((p, i) => { const mm = reviewMeshes[i].material as THREE.MeshStandardMaterial; mm.color.set(stateCol(p.state)); mm.emissive.set(stateCol(p.state)); });
      clearGroup(structure);
      const n = m.groups.length, span = Math.min(5.2, Math.max(1, n - 1) * 1.05);
      m.groups.forEach((g, i) => {
        const x = n === 1 ? 0 : -span / 2 + (span * i) / (n - 1);
        const h = g.state === "written" ? 0.35 + Math.log2(1 + g.keywords) * 0.55 : 0.25;
        const t = new THREE.Mesh(towerGeo, mat(g.state === "written" ? COL.tower : g.state === "writing" ? COL.towerWriting : COL.towerPlanned, g.state === "writing" ? 0.6 : 0.2));
        t.scale.set(1, h, 1); t.position.set(x, h / 2, 0); t.userData = { h, writing: g.state === "writing" }; structure.add(t);
        for (let k = 0; k < Math.min(g.ads, 3); k++) { const s = new THREE.Mesh(slabGeo, mat(COL.ad, 0.15)); s.position.set(x, h + 0.08 + k * 0.1, 0); structure.add(s); }
      });
      clearGroup(concerns);
      for (let i = 0; i < Math.min(m.challenger.objections, 24); i++) {
        const shard = new THREE.Mesh(new THREE.TetrahedronGeometry(0.14 + (i < m.challenger.high ? 0.06 : 0), 0), mat(i < m.challenger.high ? COL.high : COL.concern, 0.5));
        shard.userData = { a: (i / Math.max(1, m.challenger.objections)) * Math.PI * 2, r: 1.15 + (i % 3) * 0.18, y: ((i % 5) - 2) * 0.16 };
        concerns.add(shard);
      }
      const vm = verdictRing.material as THREE.MeshBasicMaterial;
      const v = m.challenger.verdict;
      vm.color.set(v === "APPROVE" ? COL.done : v === "REJECT" ? COL.fail : v ? COL.skip : m.challenger.state === "skipped" ? COL.skip : COL.wait);
      vm.opacity = v || m.challenger.state === "skipped" || m.challenger.state === "failed" ? 0.85 : 0;
      (chalCore.material as THREE.MeshStandardMaterial).emissiveIntensity = m.challenger.state === "idle" || m.challenger.state === "skipped" ? 0.08 : 0.4;
      (mainCore.material as THREE.MeshStandardMaterial).color.set(m.main.state === "failed" ? COL.fail : COL.main);
    };
    api.current = { set };
    set(model, live);

    const resize = () => { const w = el.clientWidth, h = el.clientHeight; renderer.setSize(w, h); camera.aspect = w / Math.max(h, 1); camera.updateProjectionMatrix(); };
    const ro = new ResizeObserver(resize); ro.observe(el); resize();
    let raf = 0; const clock = new THREE.Clock();
    const tick = () => {
      const t = clock.getElapsedTime();
      const requesting = isLive && cur.main.state === "requesting";
      const reviewing = isLive && cur.challenger.state === "reviewing";
      const motion = !reduced;
      mainCore.rotation.y += motion ? (requesting ? 0.012 : 0.002) : 0;
      chalCore.rotation.y -= motion ? (reviewing ? 0.012 : 0.002) : 0;
      coreLight.intensity = requesting ? 2.2 + (motion ? Math.sin(t * 3) * 0.6 : 0) : cur.main.state === "waiting" ? 0.8 : 0.3;
      rim.intensity = reviewing ? 2.2 + (motion ? Math.sin(t * 3.4) * 0.6 : 0) : 0.25;
      mainHalo.scale.setScalar(requesting && motion ? 1 + Math.sin(t * 2.4) * 0.06 : 1);
      // the stream exists only while a drafting request is outstanding
      stream.visible = requesting;
      if (requesting) {
        for (let i = 0; i < N; i++) { const u = (seeds[i] + (motion ? t * 0.22 : 0)) % 1; const p = curve.getPoint(u); sPos[i * 3] = p.x; sPos[i * 3 + 1] = p.y + Math.sin(u * 12 + i) * 0.05; sPos[i * 3 + 2] = p.z + Math.cos(u * 9 + i) * 0.08; }
        sGeo.attributes.position.needsUpdate = true;
      }
      (scan.material as THREE.MeshBasicMaterial).opacity = reviewing ? 0.07 + (motion ? (Math.sin(t * 2.2) + 1) * 0.04 : 0) : 0;
      structure.children.forEach((o: any) => { if (o.userData?.writing && motion) o.material.emissiveIntensity = 0.45 + Math.sin(t * 4) * 0.25; });
      concerns.children.forEach((o: any) => { const d = o.userData; const a = d.a + (motion ? t * 0.35 : 0); o.position.set(Math.cos(a) * d.r, d.y, Math.sin(a) * d.r); if (motion) { o.rotation.x += 0.01; o.rotation.y += 0.014; } });
      controls.update();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => {
      cancelAnimationFrame(raf); ro.disconnect(); controls.dispose();
      renderer.domElement.removeEventListener("webglcontextlost", lost);
      scene.traverse((o: any) => { o.geometry?.dispose?.(); o.material?.dispose?.(); });
      renderer.dispose(); el.removeChild(renderer.domElement); api.current = null;
    };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { api.current?.set(model, live); }, [model, live]);
  return <div ref={host} className="absolute inset-0" />;
}
