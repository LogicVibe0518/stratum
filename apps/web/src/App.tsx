import { useSyncExternalStore } from "react";
import { currentSession, onSessionChange } from "./lib/api";
import { route, usePath } from "./lib/router";
import { Editor } from "./editor/Editor";
import { Login } from "./screens/Login";
import { Missing, Posts } from "./screens/Posts";

export function App() {
  const session = useSyncExternalStore(onSessionChange, currentSession);
  const at = route(usePath());
  if (!session) return <Login />;
  if (at.screen === "editor") return <Editor key={at.post} postId={at.post} />;
  if (at.screen === "missing") return <Missing />;
  return <Posts />;
}
