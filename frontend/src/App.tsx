import { useCallback, useEffect, useState } from "react";
import {
  fetchAgents,
  fetchFeatures,
  fetchPreflight,
  fetchSandboxes,
  fetchWorkspaces,
  type AgentInfo,
  type Feature,
  type Preflight,
  type SandboxSummary,
  type WorkspaceList,
} from "./api";
import { BrandMark } from "./components/BrandMark";
import { PreflightBanner } from "./components/PreflightBanner";
import { ThemeSwitch } from "./components/ThemeSwitch";
import { useJobStream } from "./hooks/useJobStream";
import { useTheme } from "./hooks/useTheme";
import { CompareView } from "./views/CompareView";
import { SetupView } from "./views/SetupView";

type Tab = "compare" | "setup";

export function App() {
  const { jobs, connected, error, refresh } = useJobStream();
  const [tab, setTab] = useState<Tab>("compare");
  const [theme, setTheme] = useTheme();
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [features, setFeatures] = useState<Feature[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [sandboxes, setSandboxes] = useState<SandboxSummary[]>([]);
  const [workspaces, setWorkspaces] = useState<WorkspaceList>({
    active: null,
    workspaces: [],
  });

  const loadSandboxes = useCallback(() => {
    fetchSandboxes().then(setSandboxes).catch(() => setSandboxes([]));
  }, []);

  // Re-read everything that depends on which repo is selected.
  const loadWorkspaceData = useCallback(() => {
    fetchFeatures().then(setFeatures).catch(() => setFeatures([]));
    fetchWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspaces({ active: null, workspaces: [] }));
    fetchPreflight().then(setPreflight).catch(() => setPreflight(null));
  }, []);

  useEffect(() => {
    loadWorkspaceData();
    fetchAgents().then(setAgents).catch(() => setAgents([]));
    loadSandboxes();
  }, [loadSandboxes, loadWorkspaceData]);

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header__inner">
          <div className="brand">
            <BrandMark />
            {/* One text node: splitting it for two-tone weights makes the
                accessible name "Agent Showdown". */}
            <h1>AgentShowdown</h1>
          </div>

          {/* A tab strip rather than a router: two views do not justify a new
              dependency and a URL scheme. */}
          <nav role="tablist" aria-label="Sections">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "compare"}
              onClick={() => setTab("compare")}
            >
              Compare
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "setup"}
              onClick={() => setTab("setup")}
            >
              Setup
            </button>
          </nav>

          <div className="app-header__actions">
            {/* Plain text, not role="status": the preflight notice is the
                page's one status region. */}
            <span className={connected ? "connection connection--live" : "connection"}>
              {connected ? "Live" : "Reconnecting…"}
            </span>

            <ThemeSwitch value={theme} onChange={setTheme} />
          </div>
        </div>
      </header>

      <main className="app-main">
        <PreflightBanner preflight={preflight} />

        {tab === "compare" ? (
          <CompareView
            jobs={jobs}
            features={features}
            agents={agents}
            sandboxes={sandboxes}
            preflight={preflight}
            error={error}
            onLaunched={() => {
              refresh();
              loadSandboxes();
            }}
            loadSandboxes={loadSandboxes}
          />
        ) : (
          <SetupView
            features={features}
            agents={agents}
            workspaces={workspaces}
            onChanged={() => {
              loadWorkspaceData();
              refresh();
            }}
          />
        )}
      </main>
    </div>
  );
}
