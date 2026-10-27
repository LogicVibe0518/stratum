import { useQuery } from "@tanstack/react-query";
import { LogOut, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button, IconButton } from "../ui/Button";
import { Logo } from "../ui/Logo";
import { currentSession, getBlob, getJSON, sentence, signOut } from "../lib/api";
import { CLASSIC, navigate } from "../lib/router";
import type { Client, PostDetail, PostRow } from "../lib/types";

const STATUS: Record<PostRow["status"], string> = {
  draft: "Draft",
  review: "In review",
  approved: "Approved",
  archived: "Archived",
};

export function Posts() {
  const clients = useQuery({ queryKey: ["clients"], queryFn: () => getJSON<Client[]>("/api/clients") });
  const posts = useQuery({ queryKey: ["posts"], queryFn: () => getJSON<PostRow[]>("/api/posts") });
  const [client, setClient] = useState<string | null>(null);
  const names = useMemo(() => new Map((clients.data ?? []).map((one) => [one.id, one.name])), [clients.data]);
  const shown = (posts.data ?? []).filter(
    (post) => post.status !== "archived" && (!client || post.client_id === client),
  );

  const demo = currentSession()?.demo === true;

  return (
    <main className="page">
      <header className="page-head">
        <Logo />
        <span className="sep" aria-hidden />
        <h1 className="grow">Posts</h1>
        {demo ? null : (
          <Button variant="secondary" onClick={() => window.location.assign(CLASSIC)}>
            <Plus size={16} /> New post
          </Button>
        )}
        <IconButton label="Sign out" onClick={signOut}>
          <LogOut size={16} />
        </IconButton>
      </header>
      {demo ? (
        <p className="demo-banner" role="note">
          <b>Shared demo.</b> Open a post and change anything: drag, resize, type on it, try the three
          designs, swap photos, ask Stratum in plain English, share a review link. Fictional clients; uploads
          and new posts are switched off here.
        </p>
      ) : null}
      {(clients.data?.length ?? 0) > 1 ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }} role="group" aria-label="Clients">
          <button
            type="button"
            className="chip"
            aria-pressed={client === null}
            onClick={() => setClient(null)}
          >
            All clients
          </button>
          {clients.data?.map((one) => (
            <button
              key={one.id}
              type="button"
              className="chip"
              aria-pressed={client === one.id}
              onClick={() => setClient(one.id)}
            >
              {one.name}
            </button>
          ))}
        </div>
      ) : null}
      {posts.error ? <p className="error-line">{sentence(posts.error)}</p> : null}
      <section className="post-grid" aria-busy={posts.isPending}>
        {posts.isPending
          ? Array.from({ length: 8 }, (_, i) => (
              <div key={i} className="post-card">
                <div className="post-thumb skeleton" />
                <div className="skeleton" style={{ height: 14, width: "70%", borderRadius: 4 }} />
              </div>
            ))
          : shown.map((post) => (
              <PostCard key={post.id} post={post} client={names.get(post.client_id) ?? ""} />
            ))}
      </section>
      {posts.data && shown.length === 0 ? (
        <p className="muted">
          No posts yet. <a href={CLASSIC}>Make the first one</a>, then edit it here.
        </p>
      ) : null}
    </main>
  );
}

function PostCard({ post, client }: { post: PostRow; client: string }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const watch = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setSeen(true);
          watch.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    watch.observe(node);
    return () => watch.disconnect();
  }, []);
  const detail = useQuery({
    queryKey: ["post", post.id],
    queryFn: () => getJSON<PostDetail>(`/api/posts/${post.id}`),
    enabled: seen,
  });
  const renders = detail.data?.renders ?? [];
  const render = renders.find((one) => one.aspect === "4:5") ?? renders[0];
  const picture = useQuery({
    queryKey: ["picture", render?.url],
    queryFn: async () => URL.createObjectURL(await getBlob(render?.url ?? "")),
    enabled: Boolean(render),
    staleTime: Number.POSITIVE_INFINITY,
  });
  const title = post.slug.replace(/-/g, " ");
  return (
    <button ref={ref} type="button" className="post-card" onClick={() => navigate(`/posts/${post.id}`)}>
      <div
        className="post-thumb"
        style={render ? { aspectRatio: render.aspect.replace(":", " / ") } : undefined}
      >
        {picture.data ? (
          <img src={picture.data} alt="" />
        ) : (
          <div className="skeleton" style={{ height: "100%" }} />
        )}
      </div>
      <span className="title">{title.charAt(0).toUpperCase() + title.slice(1)}</span>
      <span className="meta">
        <span className="status-dot" data-status={post.status} />
        {STATUS[post.status]}
        {client ? <> · {client}</> : null}
      </span>
    </button>
  );
}

export function Missing() {
  return (
    <main className="center-page">
      <div className="card">
        <h1>Not here</h1>
        <p className="muted">That page does not exist.</p>
        <Button variant="primary" onClick={() => navigate("/")}>
          Go to posts
        </Button>
      </div>
    </main>
  );
}
