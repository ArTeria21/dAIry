import { FormEvent, useEffect, useMemo, useState } from "react";

import { chromeTextClass, readingTextClass } from "./design/theme";
import { MapView } from "./map/MapView";
import { MemoryView } from "./memory/MemoryView";
import { SeasonsView } from "./seasons/SeasonsView";
import { getCurrentUser, login, type SessionUser } from "./services/auth";
import { Button, Card, Input, Tag } from "./ui/primitives";

export function App() {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    getCurrentUser()
      .then((currentUser) => {
        if (active) {
          setUser(currentUser);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (loading) {
    return (
      <main className="min-h-screen bg-cream-paper p-6" data-testid="app-shell">
        <p className={chromeTextClass}>LOADING</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-cream-paper text-ink-black" data-testid="app-shell">
      {user ? <AuthenticatedShell user={user} /> : <LoginScreen onLogin={setUser} />}
    </main>
  );
}

function LoginScreen({ onLogin }: { onLogin: (user: SessionUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      onLogin(await login(username, password));
    } catch {
      setError("LOGIN FAILED");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-6">
      <Card>
        <form className="grid w-[min(360px,calc(100vw-48px))] gap-5" onSubmit={handleSubmit}>
          <div className="grid gap-2">
            <h1 className="font-gerstnerprogramm text-4xl font-medium leading-[1.11] tracking-[0.012em]">
              dAIry
            </h1>
            <p className={`${readingTextClass} text-sm leading-6 text-slate`}>
              Journal analytics, read-only and private.
            </p>
          </div>
          <Input
            autoComplete="username"
            label="USERNAME"
            onChange={(event) => setUsername(event.target.value)}
            value={username}
          />
          <Input
            autoComplete="current-password"
            label="PASSWORD"
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
          {error ? (
            <p className={`${chromeTextClass} text-[11px]`} role="alert">
              {error}
            </p>
          ) : null}
          <Button disabled={submitting} type="submit" variant="orange">
            LOG IN
          </Button>
        </form>
      </Card>
    </div>
  );
}

function AuthenticatedShell({ user }: { user: SessionUser }) {
  const [route, setRoute] = useState<RouteKey>(() => routeFromHash(window.location.hash));
  const routes = useMemo(() => ["map", "seasons", "memory"] as const, []);

  useEffect(() => {
    function handleHashChange() {
      setRoute(routeFromHash(window.location.hash));
    }
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  return (
    <div className="mx-auto grid min-h-screen max-w-[1200px] grid-rows-[auto_1fr] gap-12 px-6 py-6">
      <header className="flex items-center justify-between">
        <div className="font-gerstnerprogramm text-sm font-medium">dAIry</div>
        <nav className="flex gap-6">
          {routes.map((item) => (
            <a
              className={`${chromeTextClass} text-[11px] ${route === item ? "text-signal-orange" : "text-slate"}`}
              href={`#${item}`}
              key={item}
              onClick={() => setRoute(item)}
            >
              {routeLabel(item)}
            </a>
          ))}
        </nav>
        <Tag>{user.username}</Tag>
      </header>
      <section className="border-t border-hairline pt-6">
        <h2
          aria-label={routeLabel(route)}
          className="font-gerstnerprogramm text-4xl font-medium leading-[1.11] tracking-[0.012em]"
        >
          {routeTitle(route)}
        </h2>
        {route === "map" ? (
          <div className="mt-6">
            <MapView />
          </div>
        ) : route === "seasons" ? (
          <div className="mt-6">
            <SeasonsView />
          </div>
        ) : route === "memory" ? (
          <div className="mt-6">
            <MemoryView />
          </div>
        ) : (
          <p className={`${readingTextClass} mt-4 max-w-xl text-sm leading-6 text-slate`}>
            {routeDescription(route)}
          </p>
        )}
      </section>
    </div>
  );
}

type RouteKey = "map" | "seasons" | "memory";

function routeFromHash(hash: string): RouteKey {
  const normalized = hash.replace("#", "");
  if (normalized === "seasons" || normalized === "memory") {
    return normalized;
  }
  return "map";
}

function routeLabel(route: RouteKey): string {
  return {
    map: "MAP",
    seasons: "SEASONS",
    memory: "MEMORY",
  }[route];
}

function routeDescription(route: RouteKey): string {
  return {
    map: "Embedding map foundation.",
    seasons: "Calendar and topic timeline foundation.",
    memory: "Resurfacing foundation.",
  }[route];
}

function routeTitle(route: RouteKey): string {
  return {
    map: "Embedding map",
    seasons: "Emotional seasons",
    memory: "Memory resurfacing",
  }[route];
}
