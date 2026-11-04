// Browser preview of a character version's preview.glb (architecture 13, GlbViewer): orbit,
// pan, zoom, front/side/back, and sliders that drive the facial morph targets the rig
// profile defines. The GLB carries DEF bones only and every expression/viseme as a morph.
import { Bounds, Center, ContactShadows, OrbitControls, useGLTF } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { Component, type ReactNode, Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";

type Morphs = Map<string, { mesh: THREE.Mesh; index: number }[]>;

function collectMorphs(root: THREE.Object3D): Morphs {
  const out: Morphs = new Map();
  root.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh || !m.morphTargetDictionary) return;
    for (const [name, index] of Object.entries(m.morphTargetDictionary)) {
      out.set(name, [...(out.get(name) ?? []), { mesh: m, index }]);
    }
  });
  return out;
}

/** "expr_happy_mouth" -> control "expr_happy": one slider moves brow, eyes and mouth together. */
export function controlFor(key: string): string {
  return key.startsWith("expr_") ? key.split("_").slice(0, 2).join("_") : key;
}

function Model({ url, values, onMorphs }: { url: string; values: Record<string, number>; onMorphs: (names: string[]) => void }) {
  const { scene } = useGLTF(url);
  const invalidate = useThree((s) => s.invalidate);
  const morphs = useMemo(() => collectMorphs(scene), [scene]);
  useEffect(() => onMorphs([...morphs.keys()]), [morphs, onMorphs]);
  useEffect(() => {
    for (const [key, targets] of morphs) {
      const v = values[controlFor(key)] ?? 0;
      for (const t of targets) if (t.mesh.morphTargetInfluences) t.mesh.morphTargetInfluences[t.index] = v;
    }
    invalidate(); // the canvas renders on demand
  }, [morphs, values, invalidate]);
  return <primitive object={scene} />;
}

class ViewerBoundary extends Component<{ children: ReactNode }, { error?: Error }> {
  state: { error?: Error } = {};
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    return this.state.error ? <div className="viewer-empty">3D preview unavailable: {this.state.error.message}</div> : this.props.children;
  }
}

const VIEWS: Record<string, [number, number, number]> = { front: [0, 0.9, 3.2], side: [3.2, 0.9, 0], back: [0, 0.9, -3.2] };

export function GlbViewer({ url, label }: { url: string; label?: string }) {
  const [names, setNames] = useState<string[]>([]);
  const [values, setValues] = useState<Record<string, number>>({});
  const controls = useRef<OrbitControlsImpl>(null);
  const groups = useMemo(() => {
    const controlsSet = [...new Set(names.map(controlFor))];
    return {
      expressions: controlsSet.filter((n) => n.startsWith("expr_")),
      visemes: controlsSet.filter((n) => n.startsWith("vis_")),
      eyes: controlsSet.filter((n) => !n.startsWith("expr_") && !n.startsWith("vis_")),
    };
  }, [names]);

  const look = (view: string) => {
    const c = controls.current;
    if (!c) return;
    c.object.position.set(...VIEWS[view]);
    c.target.set(0, 0.7, 0);
    c.update();
  };

  return (
    <div className="viewer">
      <div className="viewer-canvas" data-testid="glb-viewer">
        <ViewerBoundary>
        <Canvas frameloop="demand" camera={{ position: VIEWS.front, fov: 30 }} shadows dpr={[1, 2]} gl={{ preserveDrawingBuffer: true }}>
          <color attach="background" args={["#eef1f5"]} />
          {/* lights only: no environment map fetched from the internet */}
          <hemisphereLight intensity={1.1} groundColor="#b9b3a8" />
          <directionalLight position={[2, 4, 3]} intensity={2.2} castShadow />
          <directionalLight position={[-3, 2, -2]} intensity={0.8} />
          <Suspense fallback={null}>
            <Bounds fit clip observe margin={1.15}>
              <Center top>
                <Model url={url} values={values} onMorphs={setNames} />
              </Center>
            </Bounds>
            <ContactShadows position={[0, 0, 0]} opacity={0.35} scale={4} blur={2.5} far={2} />
          </Suspense>
          <OrbitControls ref={controls} makeDefault target={[0, 0.7, 0]} />
        </Canvas>
        </ViewerBoundary>
        <div className="viewer-views">
          {Object.keys(VIEWS).map((v) => (
            <button key={v} onClick={() => look(v)}>{v}</button>
          ))}
          <button onClick={() => setValues({})}>reset face</button>
        </div>
        {label && <div className="viewer-label">{label}</div>}
      </div>
      {names.length > 0 && (
        <div className="morphs">
          {(["expressions", "visemes", "eyes"] as const).map((g) => (
            <fieldset key={g}>
              <legend>{g} <small>({groups[g].length})</small></legend>
              {groups[g].map((n) => (
                <label key={n} className="slider">
                  <span>{n.replace(/^(expr|vis)_/, "")}</span>
                  <input type="range" min={0} max={1} step={0.01} value={values[n] ?? 0}
                    onChange={(e) => setValues((v) => ({ ...v, [n]: Number(e.target.value) }))} aria-label={n} />
                </label>
              ))}
            </fieldset>
          ))}
        </div>
      )}
    </div>
  );
}
