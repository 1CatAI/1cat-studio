// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Suspense, lazy, useEffect, useState } from "react";
import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  RouterProvider,
} from "@tanstack/react-router";
import { LoaderCircle } from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { TooltipProvider } from "@/onecat/ui";
import { Toaster } from "sonner";
import { useQuery, mutation } from "./api";
import { ErrorNotice, Field, useText } from "./common";
import { Shell } from "./workspace-shell";
import { SetupPage } from "./setup";
import { ModelsPage } from "./models-page";
import { ChatPage } from "./chat-page";
import { AgentPage } from "./agent-page";
import { PerformancePage } from "./performance-page";
import { ServicePage } from "./service-page";
import { SettingsPage, applyTheme } from "./settings-page";
const CreativePage = lazy(() => import("./creative/page"));
const CanvasPage = lazy(() => import("./canvas/page"));

const rootRoute = createRootRoute({ component: Shell });
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: SetupPage,
});
const chatRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/chat",
  validateSearch: (search: Record<string, unknown>) => ({
    thread: typeof search.thread === "string" ? search.thread : undefined,
  }),
  component: ChatPage,
});
const canvasRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/canvas",
  component: () => (
    <Suspense
      fallback={
        <div className="oc-empty">
          <LoaderCircle className="animate-spin" />
        </div>
      }
    >
      <CanvasPage />
    </Suspense>
  ),
});
const creativeRoute = createRoute({
  getParentRoute: () => rootRoute, path: "/creative",
  component: () => <Suspense fallback={<div className="oc-empty"><LoaderCircle className="animate-spin" /></div>}><CreativePage /></Suspense>,
});
const modelsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/models",
  component: ModelsPage,
});
const agentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agent",
  validateSearch: (search: Record<string, unknown>) => ({
    task: typeof search.task === "string" ? search.task : undefined,
    source: typeof search.source === "string" ? search.source : undefined,
  }),
  component: AgentPage,
});
const performanceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/performance",
  component: PerformancePage,
});
const serviceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/service",
  component: ServicePage,
});
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: SettingsPage,
});
const setupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/setup",
  component: SetupPage,
});
const router = createRouter({
  routeTree: rootRoute.addChildren([
    indexRoute,
    chatRoute,
    canvasRoute,
    creativeRoute,
    agentRoute,
    modelsRoute,
    performanceRoute,
    serviceRoute,
    settingsRoute,
    setupRoute,
  ]),
  defaultNotFoundComponent: () => (
    <div className="oc-empty">
      <h1>404</h1>
      <Link to="/chat">1Cat Studio</Link>
    </div>
  ),
});
// This application intentionally has its own entrypoint; the upstream router is not initialized.

export function App() {
  const t = useText();
  const { data, error, refresh } = useQuery<{
    initialized: boolean;
    authenticated: boolean;
  }>("/api/auth/status");
  useEffect(() => {
    window.addEventListener("onecat:auth-expired", refresh);
    return () => window.removeEventListener("onecat:auth-expired", refresh);
  }, [refresh]);
  const [password, setPassword] = useState(""),
    [busy, setBusy] = useState(false),
    [loginError, setLoginError] = useState("");
  useEffect(() => {
    const theme = localStorage.getItem("onecat_theme") || "system";
    applyTheme(theme);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => {
      if ((localStorage.getItem("onecat_theme") || "system") === "system")
        applyTheme("system");
    };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return (
    <TooltipProvider>
      <Toaster richColors position="bottom-right" />
      {!data ? (
        <div className="oc-login-screen">
          <img
            className="oc-brand-logo oc-login-logo"
            src="/onecat.jpg?v=20260907b"
            alt=""
            draggable={false}
          />
          <h1>1Cat Studio</h1>
          {error ? (
            <ErrorNotice error={error} />
          ) : (
            <LoaderCircle className="animate-spin" />
          )}
          <Button variant="outline" onClick={refresh}>
            {t("重试连接", "Retry connection")}
          </Button>
        </div>
      ) : data.authenticated ? (
        <RouterProvider router={router} />
      ) : (
        <div className="oc-login-screen">
          <form
            className="oc-login"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setLoginError("");
              try {
                await mutation(
                  data.initialized ? "/api/auth/login" : "/api/auth/setup",
                  { password },
                );
                setPassword("");
                refresh();
              } catch (error) {
                setLoginError((error as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <img
              className="oc-brand-logo oc-login-logo"
              src="/onecat.jpg?v=20260907b"
              alt=""
              draggable={false}
            />
            <h1>1Cat Studio</h1>
            <p>
              {data.initialized
                ? t("登录本地推理工作台", "Sign in to your inference workbench")
                : t(
                    "创建管理员密码，开始配置这台机器。",
                    "Create an administrator password to set up this machine.",
                  )}
            </p>
            <Field
              label={
                data.initialized
                  ? t("密码", "Password")
                  : t(
                      "密码（至少 8 个字符）",
                      "Password (at least 8 characters)",
                    )
              }
            >
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={data.initialized ? 1 : 8}
                maxLength={256}
                autoComplete={
                  data.initialized ? "current-password" : "new-password"
                }
                required
                autoFocus
              />
            </Field>
            <ErrorNotice error={loginError} />
            <Button type="submit" disabled={busy}>
              {busy && <LoaderCircle className="animate-spin" />}
              {data.initialized
                ? t("登录", "Sign in")
                : t("创建并继续", "Create and continue")}
            </Button>
            <small>
              1Cat-vLLM · {t("本地 AI 工作台", "Local AI workspace")}
            </small>
          </form>
        </div>
      )}
    </TooltipProvider>
  );
}
