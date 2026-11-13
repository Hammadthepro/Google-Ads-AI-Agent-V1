/** WebGL renderer for a research scene. Loaded lazily (it carries three.js) and only when WebGL is available; the SVG view stands in
 *  while it loads and if it ever fails. It draws exactly the nodes in the graph it is given and nothing else. */
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { nameOrder, SCENE_PALETTE as C, type SceneGraph, type SceneNode } from "./sceneLayout";

type Props = { graph: SceneGraph; focus: string | null; onFocus: (id: string | null) => void; live: boolean; reduced: boolean; onFail: () => void; resetKey: number };

const roleColor = (n: SceneNode): string =>
  n.role === "cluster" || n.role === "term" ? C.series[(n.tint ?? 0) % C.series.length]
    : (({ hub: C.hub, page: C.page, working: C.working, failed: C.failed, blocked: C.blocked, competitor: C.competitor, business: C.business, listing: C.listing, host: C.host } as Record<string, string>)[n.role] ?? C.page);

/** One point. All points share a single instanced mesh, so a 600-page crawl is one draw call, not 600. */
type Entry = { node: SceneNode; pos: THREE.Vector3; target: THREE.Vector3; born: number; idx: number };
const seconds = () => performance.now() / 1000;

export default function Scene3D({ graph, focus, onFocus, live, reduced, onFail, resetKey }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);
  const api = useRef<{ setGraph: (g: SceneGraph) => void; setFocus: (id: string | null) => void; setLive: (l: boolean) => void; reset: () => void } | null>(null);
  const onFocusRef = useRef(onFocus); onFocusRef.current = onFocus;

  useEffect(() => {
    const el = host.current!, labelLayer = labelsRef.current!;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "low-power" });
    } catch { onFail(); return; }
    const small = el.clientWidth < 640;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, small ? 1.25 : 1.75));
    renderer.setClearColor(C.bg);
    renderer.domElement.style.display = "block";
    renderer.domElement.setAttribute("aria-hidden", "true");
    el.appendChild(renderer.domElement);
    const onLost = (e: Event) => { e.preventDefault(); onFail(); };
    renderer.domElement.addEventListener("webglcontextlost", onLost);

    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(C.bg, 18, 46);
    const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 200);
    const controls = new OrbitControls(camera, renderer.domElement);
    Object.assign(controls, { enableDamping: true, dampingFactor: 0.08, enablePan: false, enableZoom: false, minPolarAngle: 0.25, maxPolarAngle: 1.32, autoRotateSpeed: 0.35 });
    controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE };
    // touch: one finger scrolls the page (the scene must never trap scrolling on a phone); two fingers turn the scene
    controls.touches = { ONE: null as unknown as THREE.TOUCH, TWO: THREE.TOUCH.DOLLY_ROTATE };
    renderer.domElement.style.touchAction = "pan-y";
    let userMoved = false;
    controls.addEventListener("start", () => { userMoved = true; dirty = true; });
    controls.addEventListener("change", () => { dirty = true; });

    scene.add(new THREE.HemisphereLight("#cfe3e6", "#0b1218", 1.1));
    const key = new THREE.DirectionalLight("#ffffff", 1.4); key.position.set(6, 12, 8); scene.add(key);

    // ground: a quiet polar grid; rings and islands are drawn on top of it
    const ground = new THREE.Group(); scene.add(ground);
    const gridMat = new THREE.LineBasicMaterial({ color: C.grid, transparent: true, opacity: 0.9 });
    const buildGround = (r: number) => {
      ground.children.forEach((c: any) => c.geometry?.dispose?.()); ground.clear();
      const pts: number[] = [];
      for (let k = 1; k <= 6; k++) { const rr = (r * 1.25 * k) / 6; for (let i = 0; i < 96; i++) { const a = (i / 96) * Math.PI * 2, b = ((i + 1) / 96) * Math.PI * 2; pts.push(Math.cos(a) * rr, -0.02, Math.sin(a) * rr, Math.cos(b) * rr, -0.02, Math.sin(b) * rr); } }
      for (let i = 0; i < 12; i++) { const a = (i / 12) * Math.PI * 2; pts.push(0, -0.02, 0, Math.cos(a) * r * 1.25, -0.02, Math.sin(a) * r * 1.25); }
      const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      ground.add(new THREE.LineSegments(g, gridMat));
    };

    const sphere = new THREE.IcosahedronGeometry(1, 2);
    const sphereLow = new THREE.IcosahedronGeometry(1, 1);   // large crawls: a quarter of the triangles, same silhouette at that size
    const haloGeo = new THREE.RingGeometry(1.35, 1.6, 48);
    const focusMat = new THREE.MeshBasicMaterial({ color: "#ffffff", side: THREE.DoubleSide, transparent: true, opacity: 0.9 });
    const focusRing = new THREE.Mesh(haloGeo, focusMat); focusRing.visible = false; scene.add(focusRing);
    const stemMat = new THREE.LineBasicMaterial({ color: C.edge, transparent: true, opacity: 0.8 });
    const instMat = new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.5, metalness: 0.05, emissive: "#16242e" });
    const bgColor = new THREE.Color(C.bg);

    const nodes = new Map<string, Entry>();
    let order: Entry[] = [];
    let inst: THREE.InstancedMesh | null = null;
    let cap = 0;
    let settling = false;
    const edgeGroup = new THREE.Group(); scene.add(edgeGroup);
    const ringGroup = new THREE.Group(); scene.add(ringGroup);
    const islandGroup = new THREE.Group(); scene.add(islandGroup);
    const stemGroup = new THREE.Group(); scene.add(stemGroup);
    let current: SceneGraph | null = null;
    let focusId: string | null = null;
    let isLive = false;
    let dirty = true;
    let camDist = 14;

    const disposeGroup = (g: THREE.Group) => { g.traverse((o: any) => { o.geometry?.dispose?.(); if (o.material && o.material !== stemMat && o.material !== gridMat) o.material.dispose?.(); }); g.clear(); };
    const lineGeo = (pairs: [THREE.Vector3, THREE.Vector3][]) => { const g = new THREE.BufferGeometry(); g.setFromPoints(pairs.flat()); return g; };

    const M = new THREE.Matrix4(), Q = new THREE.Quaternion(), S = new THREE.Vector3(), P = new THREE.Vector3();
    const headY = (n: SceneNode) => (n.pin ? n.size * 3.2 : 0);
    const writeMatrix = (e: Entry, scale: number) => {
      P.copy(e.pos); P.y += headY(e.node);
      inst!.setMatrixAt(e.idx, M.compose(P, Q, S.setScalar(Math.max(scale, 0.0001))));
    };
    const ensureCapacity = (n: number) => {
      if (inst && n <= cap) return;
      if (inst) { scene.remove(inst); inst.dispose(); }
      cap = Math.max(64, 2 ** Math.ceil(Math.log2(Math.max(n, 1))));
      inst = new THREE.InstancedMesh(n > 300 ? sphereLow : sphere, instMat, cap);
      inst.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      inst.frustumCulled = false;
      scene.add(inst);
    };

    const setGraph = (g: SceneGraph): boolean => {
      const now = seconds();
      const fresh = g.nodes.filter((n) => !nodes.has(n.id)).length;
      // a whole finished audit arrives at once: place it, don't animate hundreds of points in one by one
      const instant = reduced || fresh > 40;
      const seen = new Set<string>();
      for (const n of g.nodes) {
        seen.add(n.id);
        let e = nodes.get(n.id);
        if (!e) {
          e = { node: n, pos: new THREE.Vector3(n.x, n.y, n.z), target: new THREE.Vector3(n.x, n.y, n.z), born: instant ? -10 : now, idx: 0 };
          nodes.set(n.id, e);
        }
        e.node = n;
        e.target.set(n.x, n.y, n.z);
        if (instant) e.pos.copy(e.target);
      }
      for (const id of [...nodes.keys()]) if (!seen.has(id)) nodes.delete(id);
      order = g.nodes.map((n) => nodes.get(n.id)!);
      ensureCapacity(order.length);
      const geo = order.length > 300 ? sphereLow : sphere;
      if (inst!.geometry !== geo) inst!.geometry = geo;
      const c = new THREE.Color();
      order.forEach((e, i) => {
        e.idx = i;
        c.set(roleColor(e.node));
        if (e.node.dim) c.lerp(bgColor, 0.68);
        inst!.setColorAt(i, c);
        const grown = now - e.born >= 0.45;
        writeMatrix(e, grown ? e.node.size : 0.0001);
      });
      inst!.count = order.length;
      inst!.instanceMatrix.needsUpdate = true;
      if (inst!.instanceColor) inst!.instanceColor.needsUpdate = true;
      settling = true;

      disposeGroup(stemGroup);
      const stems = g.nodes.filter((n) => n.pin).map((n) => [new THREE.Vector3(n.x, 0, n.z), new THREE.Vector3(n.x, n.size * 3.2, n.z)] as [THREE.Vector3, THREE.Vector3]);
      if (stems.length) stemGroup.add(new THREE.LineSegments(lineGeo(stems), stemMat));

      disposeGroup(edgeGroup);
      const index = new Map(g.nodes.map((n) => [n.id, n]));
      const byId = (id: string) => index.get(id);
      const solid: [THREE.Vector3, THREE.Vector3][] = [], faint: [THREE.Vector3, THREE.Vector3][] = [];
      for (const ed of g.edges) {
        const a = byId(ed.a), b = byId(ed.b); if (!a || !b) continue;
        (ed.faint || ed.dim ? faint : solid).push([new THREE.Vector3(a.x, a.y, a.z), new THREE.Vector3(b.x, b.y, b.z)]);
      }
      if (solid.length) edgeGroup.add(new THREE.LineSegments(lineGeo(solid), new THREE.LineBasicMaterial({ color: C.edge, transparent: true, opacity: 0.85 })));
      if (faint.length) edgeGroup.add(new THREE.LineSegments(lineGeo(faint), new THREE.LineBasicMaterial({ color: C.edge, transparent: true, opacity: 0.38 })));

      disposeGroup(ringGroup);
      for (const r of g.rings) {
        const pts = Array.from({ length: 129 }, (_, i) => { const a = (i / 128) * Math.PI * 2; return new THREE.Vector3(r.x + Math.cos(a) * r.r, 0, r.z + Math.sin(a) * r.r); });
        const geo = new THREE.BufferGeometry().setFromPoints(pts);
        ringGroup.add(new THREE.Line(geo, new THREE.LineDashedMaterial({ color: C.gridStrong, dashSize: 0.18, gapSize: 0.14, transparent: true, opacity: r.dim ? 0.35 : 1 })));
        (ringGroup.children[ringGroup.children.length - 1] as THREE.Line).computeLineDistances();
      }

      disposeGroup(islandGroup);
      for (const is of g.islands) {
        const stateColor = is.state === "running" ? C.working : is.state === "done" ? "#7fae95" : is.state === "failed" ? C.failed : C.gridStrong;
        const plate = new THREE.Mesh(new THREE.CircleGeometry(is.w / 2, 64), new THREE.MeshBasicMaterial({ color: "#13202b", transparent: true, opacity: is.state === "waiting" ? 0.55 : 0.9 }));
        plate.rotation.x = -Math.PI / 2; plate.position.set(is.x, -0.04, is.z); islandGroup.add(plate);
        const pts = Array.from({ length: 97 }, (_, i) => { const a = (i / 96) * Math.PI * 2; return new THREE.Vector3(is.x + Math.cos(a) * is.w / 2, -0.03, is.z + Math.sin(a) * is.w / 2); });
        islandGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: stateColor, transparent: true, opacity: 0.9 })));
      }

      if (!current || Math.abs(current.radius - g.radius) > 0.01 || current.kind !== g.kind) {
        buildGround(g.kind === "combined" ? g.radius * 0.7 : g.radius);
        scene.fog = new THREE.Fog(C.bg, Math.max(g.radius, 5) * 2.4, Math.max(g.radius, 5) * 6);
      }
      camDist = g.kind === "combined" ? g.radius * 1.5 : Math.max(g.radius, 5) * 2.55;
      current = g;
      dirty = true;
      return instant;
    };

    /** Sets the viewing direction once; afterwards only the distance follows the graph's size, so auto-rotation is never fought. */
    let placed = false;
    const placeCamera = (instant: boolean, dt = 1 / 60): boolean => {
      // a scene mounted while hidden (inside a closed <details>, a collapsed tab) has no size yet: wait for one
      if (!el.clientWidth || !el.clientHeight || !current) return false;
      if (!placed || !Number.isFinite(camera.position.length())) { instant = true; placed = true; }
      const aspect = el.clientWidth / Math.max(el.clientHeight, 1);
      // a row of islands must fit the width: solve the distance from the horizontal field of view
      const hHalf = Math.atan(Math.tan((camera.fov * Math.PI) / 360) * aspect);
      const d = current?.kind === "combined" ? Math.max(camDist, (current.radius * 1.28) / Math.tan(hHalf)) : camDist * (aspect < 1.1 ? 1.35 : 1);
      if (!Number.isFinite(d) || d <= 0) return false;
      if (instant) {
        const dir = current?.kind === "combined" ? new THREE.Vector3(0, 0.8, 0.7) : new THREE.Vector3(0.32, 0.82, 0.74);
        camera.position.copy(dir.normalize().multiplyScalar(d));
        controls.target.set(0, 0, 0);
        return false;
      }
      const L = camera.position.length();
      if (Math.abs(L - d) < 0.01) return false;
      const k = 1 - Math.pow(0.95, dt * 60);   // same pace at any frame rate
      camera.position.multiplyScalar((reduced || Math.abs(L - d) < 0.05 ? d : L + (d - L) * k) / L);
      return true;
    };

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h, false); renderer.domElement.style.width = `${w}px`; renderer.domElement.style.height = `${h}px`;
      camera.aspect = w / Math.max(h, 1); camera.updateProjectionMatrix(); dirty = true;
      if (!userMoved && w && h) placeCamera(!placed);
    };
    const ro = new ResizeObserver(resize); ro.observe(el); resize();

    // hover picking in screen space: nearest point under the pointer. Cheap for thousands of points, unlike triangle ray tests.
    let lastHover: string | null = null;
    const proj = new THREE.Vector3(), edge = new THREE.Vector3();
    const onMove = (ev: PointerEvent) => {
      const r = renderer.domElement.getBoundingClientRect();
      const mx = ev.clientX - r.left, my = ev.clientY - r.top;
      let best: Entry | null = null, bestD = Infinity;
      for (const e of order) {
        proj.copy(e.pos); proj.y += headY(e.node); edge.copy(proj); edge.y += e.node.size;
        proj.project(camera); if (proj.z > 1) continue;
        edge.project(camera);
        const sx = (proj.x * 0.5 + 0.5) * r.width, sy = (-proj.y * 0.5 + 0.5) * r.height;
        const rad = Math.max(Math.abs(((-edge.y * 0.5 + 0.5) * r.height) - sy), 5) + 2;
        const dd = Math.hypot(sx - mx, sy - my);
        if (dd <= rad && dd < bestD) { best = e; bestD = dd; }
      }
      const id = best ? best.node.id : null;
      renderer.domElement.style.cursor = id ? "pointer" : "grab";
      if (id !== lastHover) { lastHover = id; onFocusRef.current(id); }
    };
    const onLeave = () => { if (lastHover) { lastHover = null; onFocusRef.current(null); } };
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("pointerleave", onLeave);

    // labels: hubs, islands and rings always; the focused node and the newest few while live
    const labelEls = new Map<string, HTMLDivElement>();
    const v = new THREE.Vector3();
    const drawLabels = () => {
      if (!current) return;
      const w = el.clientWidth, h = el.clientHeight;
      const want: { id: string; text: string; sub?: string; pos: THREE.Vector3; kind: string }[] = [];
      for (const n of current.nodes) if (n.hub) want.push({ id: n.id, text: n.label, pos: new THREE.Vector3(n.x, n.y + n.size + 0.35, n.z), kind: "hub" });
      for (const is of current.islands) want.push({ id: `island:${is.id}`, text: is.label, sub: is.state === "running" ? "researching" : is.state === "done" ? "done" : is.state === "failed" ? "failed" : is.state === "skipped" ? "skipped" : "not started", pos: new THREE.Vector3(is.x, 0, is.z + is.d / 2 + 0.25), kind: "island" });
      for (const r of current.rings) if (r.label && !r.dim) want.push({ id: `ring:${r.x}:${r.r}`, text: r.label, pos: new THREE.Vector3(r.x + r.r * 0.71, 0, r.z + r.r * 0.71), kind: "ring" });
      // the things a scene is about (competitors, listings, keyword sources) are named whenever there are few enough to read
      for (const n of nameOrder(current.nodes, w >= 640 ? 12 : 6)) want.push({ id: n.id, text: n.label, pos: new THREE.Vector3(n.x, n.y + (n.pin ? n.size * 3.2 : 0) + n.size + 0.2, n.z), kind: "recent" });
      if (isLive && w >= 520) {
        const newest = current.nodes.filter((n) => !n.hub && n.role !== "term" && !n.dim).sort((a, b) => b.seq - a.seq).slice(0, 3);
        for (const n of newest) if (!want.some((x) => x.id === n.id)) want.push({ id: n.id, text: n.label, pos: new THREE.Vector3(n.x, n.y + n.size + 0.2, n.z), kind: "recent" });
      }
      const f = focusId ? current.nodes.find((n) => n.id === focusId) : null;
      if (f) want.push({ id: `focus`, text: f.label, sub: f.sub, pos: new THREE.Vector3(f.x, f.y + f.size + 0.25, f.z), kind: "focus" });
      const keep = new Set<string>();
      const placedRects: { l: number; t: number; r: number; b: number }[] = [];   // labels never sit on top of each other
      for (const l of want) {
        if (l.kind === "recent" && f && l.id === f.id) continue;
        keep.add(l.id);
        let d = labelEls.get(l.id);
        if (!d) {
          d = document.createElement("div");
          d.className = l.kind === "focus" ? "pointer-events-none absolute left-0 top-0 z-10 max-w-[260px] rounded-md bg-white px-2.5 py-1.5 text-xs leading-4 text-slate-900 shadow-pop"
            : l.kind === "island" ? "pointer-events-none absolute left-0 top-0 text-center text-xs font-medium text-slate-200"
            : l.kind === "ring" ? "pointer-events-none absolute left-0 top-0 font-mono text-[10px] text-ink-muted"
            : l.kind === "hub" ? "pointer-events-none absolute left-0 top-0 max-w-[200px] truncate rounded bg-ink/80 px-1.5 py-0.5 text-xs font-medium text-white"
            : "pointer-events-none absolute left-0 top-0 max-w-[180px] truncate rounded bg-ink/70 px-1.5 py-0.5 text-[11px] text-slate-300";
          labelLayer.appendChild(d); labelEls.set(l.id, d);
        }
        const html = l.kind === "focus" ? `<div class="font-medium break-words">${esc(l.text)}</div>${l.sub ? `<div class="mt-0.5 text-slate-500 break-words">${esc(l.sub)}</div>` : ""}`
          : l.kind === "island" ? `${esc(l.text)}<div class="text-[10px] font-normal text-ink-muted">${esc(l.sub ?? "")}</div>` : esc(l.text);
        if (d.dataset.html !== html) { d.innerHTML = html; d.dataset.html = html; }
        v.copy(l.pos).project(camera);
        const x = (v.x * 0.5 + 0.5) * w, y = (-v.y * 0.5 + 0.5) * h;
        let hidden = v.z > 1 || x < -40 || x > w + 40 || y < -20 || y > h + 20;
        if (!hidden && l.kind !== "focus") {
          d.style.display = "block";
          const bw = d.offsetWidth, bh = d.offsetHeight;
          const top = l.kind === "island" || l.kind === "ring" ? y : y - bh;
          const rect = { l: x - bw / 2 - 2, t: top - 1, r: x + bw / 2 + 2, b: top + bh + 1 };
          if (placedRects.some((p) => rect.l < p.r && rect.r > p.l && rect.t < p.b && rect.b > p.t)) hidden = true;
          else placedRects.push(rect);
        }
        d.style.display = hidden ? "none" : "block";
        d.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, ${l.kind === "island" || l.kind === "ring" ? "0" : "-100%"})`;
      }
      for (const [id, d] of labelEls) if (!keep.has(id)) { d.remove(); labelEls.delete(id); }
    };

    let raf = 0, visible = true, running = false, last = performance.now(), interacting = false;
    controls.addEventListener("start", () => { interacting = true; });
    controls.addEventListener("end", () => { interacting = false; });
    const frame = () => {
      raf = requestAnimationFrame(frame);
      const nowMs = performance.now();
      // the slow live turn and the "opening" pulse need no more than 30 frames a second; input and new points still draw at once
      if (isLive && !dirty && !settling && !interacting && nowMs - last < 33) return;
      const dt = Math.min((nowMs - last) / 1000, 0.1); last = nowMs;
      const t = nowMs / 1000;
      let animating = false;
      const working = !reduced && order.some((e) => e.node.role === "working");
      if (inst && (settling || working)) {
        const k = 1 - Math.pow(0.92, dt * 60);
        let moved = false;
        for (const e of order) {
          const grow = reduced ? 1 : Math.min((t - e.born) / 0.45, 1);
          const near = e.pos.distanceToSquared(e.target) <= 0.0004;
          const pulse = e.node.role === "working" && !reduced;
          if (grow >= 1 && near && !pulse) { if (settling) writeMatrix(e, e.node.size); continue; }
          if (!near) e.pos.lerp(e.target, reduced ? 1 : k); else e.pos.copy(e.target);
          const ease = 1 - Math.pow(1 - grow, 3);
          writeMatrix(e, e.node.size * ease * (pulse ? 1 + Math.sin(t * 5) * 0.18 : 1));
          moved = true;
        }
        inst.instanceMatrix.needsUpdate = true;
        settling = moved;
        animating = moved;
      }
      const f = focusId ? nodes.get(focusId) : undefined;
      focusRing.visible = !!f;
      if (f) { focusRing.position.copy(f.pos); focusRing.position.y += headY(f.node); focusRing.scale.setScalar(f.node.size); focusRing.quaternion.copy(camera.quaternion); }
      controls.autoRotate = isLive && !reduced && !userMoved && current?.kind !== "combined";
      if (controls.autoRotate) animating = true;
      if (!userMoved && placeCamera(false, dt)) animating = true;
      const changed = controls.update(dt);
      if (!dirty && !animating && !changed) return;   // a focused point is redrawn when focus changes (dirty), not every frame
      dirty = false;
      renderer.render(scene, camera);
      drawLabels();
    };
    const start = () => { if (!running && visible && !document.hidden) { running = true; last = performance.now(); raf = requestAnimationFrame(frame); } };
    const stop = () => { running = false; cancelAnimationFrame(raf); };
    const io = new IntersectionObserver(([en]) => { visible = en.isIntersecting; visible ? start() : stop(); }, { threshold: 0.01 });
    io.observe(el);
    const onVis = () => (document.hidden ? stop() : start());
    document.addEventListener("visibilitychange", onVis);

    api.current = {
      setGraph: (g) => { const first = !current; const bulk = setGraph(g); if (first) placeCamera(true); else if (bulk && !userMoved) placeCamera(false, 1000); },
      setFocus: (id) => { focusId = id; dirty = true; },
      setLive: (l) => { isLive = l; dirty = true; },
      reset: () => { userMoved = false; placeCamera(true); controls.target.set(0, 0, 0); dirty = true; },
    };
    start();

    return () => {
      stop(); io.disconnect(); ro.disconnect(); document.removeEventListener("visibilitychange", onVis);
      renderer.domElement.removeEventListener("pointermove", onMove); renderer.domElement.removeEventListener("pointerleave", onLeave);
      renderer.domElement.removeEventListener("webglcontextlost", onLost);
      controls.dispose();
      if (inst) { scene.remove(inst); inst.dispose(); }
      [edgeGroup, ringGroup, islandGroup, stemGroup, ground].forEach(disposeGroup);
      sphere.dispose(); sphereLow.dispose(); haloGeo.dispose(); focusMat.dispose(); stemMat.dispose(); gridMat.dispose(); instMat.dispose();
      renderer.dispose(); renderer.forceContextLoss();
      renderer.domElement.remove();
      labelEls.forEach((d) => d.remove());
      api.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reduced]);

  useEffect(() => { api.current?.setGraph(graph); }, [graph, reduced]);
  useEffect(() => { api.current?.setFocus(focus); }, [focus, reduced]);
  useEffect(() => { api.current?.setLive(live); }, [live, reduced]);
  useEffect(() => { if (resetKey) api.current?.reset(); }, [resetKey]);

  return (
    <div className="absolute inset-0">
      <div ref={host} className="absolute inset-0 cursor-grab active:cursor-grabbing" />
      <div ref={labelsRef} className="pointer-events-none absolute inset-0 overflow-hidden" />
    </div>
  );
}

function esc(s: string) { return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!)); }
