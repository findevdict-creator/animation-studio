import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { useMe } from "./api/hooks";
import { currentUser, userManager } from "./auth";
import { AccessoryGalleryPage } from "./pages/AccessoryGalleryPage";
import { AccessoryPage } from "./pages/AccessoryPage";
import { AnimationGalleryPage } from "./pages/AnimationGalleryPage";
import { AnimationPage } from "./pages/AnimationPage";
import { AudioPage } from "./pages/AudioPage";
import { NewAnimationPage } from "./pages/NewAnimationPage";
import { CharacterPage } from "./pages/CharacterPage";
import { GalleryPage } from "./pages/GalleryPage";
import { NewCharacterPage } from "./pages/NewCharacterPage";
import { NewOutfitPage } from "./pages/NewOutfitPage";
import { OutfitGalleryPage } from "./pages/OutfitGalleryPage";
import { NewAccessoryPage } from "./pages/NewAccessoryPage";
import { OutfitPage } from "./pages/OutfitPage";
import { SceneEditorPage } from "./pages/SceneEditorPage";
import { SceneListPage } from "./pages/SceneListPage";
import { ScenePage } from "./pages/ScenePage";

let redirecting = false;
let callback: ReturnType<typeof userManager.signinRedirectCallback> | undefined; // the code is single-use

function Callback() {
  const navigate = useNavigate();
  useEffect(() => {
    callback ??= userManager.signinRedirectCallback();
    callback.then((u) => navigate((u.state as string) || "/characters", { replace: true }));
  }, [navigate]);
  return <main>signing in…</main>;
}

function Shell({ children }: { children: React.ReactNode }) {
  const me = useMe();
  return (
    <>
      <nav className="top">
        <Link to="/characters" className="brand">Studio</Link>
        <Link to="/characters">Characters</Link>
        <Link to="/outfits">Outfits</Link>
        <Link to="/accessories">Accessories</Link>
        <Link to="/animations">Animations</Link>
        <Link to="/audio">Dialogue audio</Link>
        <Link to="/scenes">Scenes</Link>
        <span className="spacer" />
        {me.data && <span className="who">{me.data.name} · {me.data.roles.join(", ")}</span>}
        <button onClick={() => userManager.signoutRedirect()}>Sign out</button>
      </nav>
      {children}
    </>
  );
}

export function App() {
  const [ready, setReady] = useState(window.location.pathname === "/callback");
  useEffect(() => {
    if (ready) return;
    currentUser().then((u) => {
      if (u) setReady(true);
      else if (!redirecting) {
        redirecting = true; // StrictMode runs effects twice in development
        userManager.signinRedirect({ state: window.location.pathname + window.location.search });
      }
    });
  }, [ready]);
  if (!ready) return <main>signing in…</main>;
  return (
    <Routes>
      <Route path="/callback" element={<Callback />} />
      <Route path="/" element={<Navigate to="/characters" replace />} />
      <Route path="/characters" element={<Shell><GalleryPage /></Shell>} />
      <Route path="/characters/new" element={<Shell><NewCharacterPage /></Shell>} />
      <Route path="/characters/:id" element={<Shell><CharacterPage /></Shell>} />
      <Route path="/outfits" element={<Shell><OutfitGalleryPage /></Shell>} />
      <Route path="/outfits/new" element={<Shell><NewOutfitPage /></Shell>} />
      <Route path="/outfits/:id" element={<Shell><OutfitPage /></Shell>} />
      <Route path="/accessories" element={<Shell><AccessoryGalleryPage /></Shell>} />
      <Route path="/accessories/new" element={<Shell><NewAccessoryPage /></Shell>} />
      <Route path="/accessories/:id" element={<Shell><AccessoryPage /></Shell>} />
      <Route path="/animations" element={<Shell><AnimationGalleryPage /></Shell>} />
      <Route path="/animations/new" element={<Shell><NewAnimationPage /></Shell>} />
      <Route path="/animations/:id" element={<Shell><AnimationPage /></Shell>} />
      <Route path="/audio" element={<Shell><AudioPage /></Shell>} />
      <Route path="/scenes" element={<Shell><SceneListPage /></Shell>} />
      <Route path="/scenes/new" element={<Shell><SceneEditorPage /></Shell>} />
      <Route path="/scenes/:id" element={<Shell><ScenePage /></Shell>} />
      <Route path="/scenes/:id/edit" element={<Shell><SceneEditorPage /></Shell>} />
    </Routes>
  );
}
