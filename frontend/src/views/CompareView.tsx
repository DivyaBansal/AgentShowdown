/** The comparison half of the app: launch runs and read the results.
 *
 * Laid out so nothing is a long scroll away. Launching a showdown is a
 * collapsible panel at the top. The results are tabs (contenders, runs,
 * head to head, how they worked, sandboxes) in a bar that stays
 * pinned under the header. Inspecting a contender opens a drawer beside the
 * board rather than a section at the bottom of the page. The open tab and
 * the grouping are remembered, so a return visit lands where you left off.
 *
 * Every tab stays mounted while hidden, so a sort order or a chart's
 * selected feature survives a trip to another tab.
 */

import { useCallback, useState, type ReactNode } from "react";
import {
  type AgentInfo,
  type Feature,
  type Job,
  type Preflight,
  type SandboxSummary,
} from "../api";
import { ComparisonSpread } from "../charts/ComparisonSpread";
import { assignSeriesColorsFor } from "../charts/palette";
import { ApproachComparison } from "../components/ApproachComparison";
import { GroupModePicker, GroupedJobs } from "../components/GroupedJobs";
import { JobInspector } from "../components/JobInspector";
import { RunLauncher } from "../components/RunLauncher";
import { SandboxControls } from "../components/SandboxControls";
import { RunsTable } from "../components/RunsTable";
import { Drawer } from "../components/ui/Drawer";
import { Notice } from "../components/ui/Notice";
import { Panel } from "../components/ui/Panel";
import { Tabs } from "../components/ui/Tabs";
import { panelId, tabId } from "../components/ui/tabIds";
import { usePref } from "../hooks/usePref";
import { GROUP_MODES, groupJobs, stateBucket, type GroupMode } from "../lib/jobs";

const SECTIONS = ["contenders", "runs", "head-to-head", "approach", "sandboxes"] as const;

type Section = (typeof SECTIONS)[number];

const SECTION_LABELS: Record<Section, string> = {
  contenders: "Contenders",
  runs: "Runs",
  "head-to-head": "Head to head",
  approach: "How they worked",
  sandboxes: "Sandboxes",
};

const GROUP = "compare";

/** The last path segment, so a long absolute path stays readable in a cell. */
function repoLabel(repo: string | null): string {
  if (!repo) return "unknown repo";
  const parts = repo.split("/").filter(Boolean);

  return parts[parts.length - 1] ?? repo;
}

/** A count beside a tab label; `urgent` for something waiting on a person. */
function Badge({ count, urgent = false, title }: { count: number; urgent?: boolean; title: string }) {
  if (count === 0) return null;

  return (
    <span className={urgent ? "tab-badge tab-badge--urgent" : "tab-badge"} title={title}>
      {count}
      <span className="visually-hidden">{` ${title}`}</span>
    </span>
  );
}

function SectionPanel({
  section,
  current,
  children,
}: {
  section: Section;
  current: Section;
  children: ReactNode;
}) {
  return (
    <div
      role="tabpanel"
      id={panelId(GROUP, section)}
      aria-labelledby={tabId(GROUP, section)}
      className="section-panel"
      hidden={section !== current}
    >
      {children}
    </div>
  );
}

export function CompareView({
  jobs,
  features,
  agents,
  sandboxes,
  preflight,
  error,
  onLaunched,
  loadSandboxes,
}: {
  jobs: Job[];
  features: Feature[];
  agents: AgentInfo[];
  sandboxes: SandboxSummary[];
  preflight: Preflight | null;
  error: string | null;
  onLaunched: () => void;
  loadSandboxes: () => void;
}) {
  const [section, setSection] = usePref<Section>("compare.section", SECTIONS, "contenders");
  const [mode, setMode] = usePref<GroupMode>("compare.groupMode", GROUP_MODES, "state");
  // Only the name is kept in state, not the Job snapshot itself: `jobs`
  // keeps arriving over SSE (including live log_tail updates), and looking
  // the job up fresh every render is what keeps the inspector's output
  // actually live instead of frozen at the moment it was opened.
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [repoFilter, setRepoFilter] = useState("");
  const closeInspector = useCallback(() => setSelectedName(null), []);

  // One database now holds every workspace's history, so the board needs a
  // way to narrow to the repo in hand.
  const repos = [...new Set(jobs.map((j) => j.repo).filter((r): r is string => r !== null))];
  const visible = repoFilter === "" ? jobs : jobs.filter((j) => j.repo === repoFilter);

  // Built from the full roster, so filtering can never renumber the palette.
  const colorFor = assignSeriesColorsFor(jobs);

  // The inspector steps through contenders in the order the board shows them.
  const ordered = groupJobs(visible, mode).flatMap((group) => group.jobs);
  const selectedIndex = ordered.findIndex((j) => j.sandbox_name === selectedName);
  const selectedJob = ordered[selectedIndex] ?? null;
  const previous = ordered[selectedIndex - 1];
  const next = selectedIndex === -1 ? undefined : ordered[selectedIndex + 1];

  const waiting = visible.filter((j) => stateBucket(j.status) === "needs_input").length;

  const badges: Partial<Record<Section, ReactNode>> = {
    contenders: <Badge count={waiting} urgent title="need input" />,
    sandboxes: <Badge count={sandboxes.length} title="running" />,
  };

  return (
    <div className="compare">
      <div className="view-head">
        <div>
          <h2>Compare</h2>
          <p>Same task, several agents, each in its own sandbox.</p>
        </div>
        {repos.length > 1 && (
          <div className="toolbar">
            <label htmlFor="repo-filter">Show repository</label>
            <select
              id="repo-filter"
              value={repoFilter}
              onChange={(e) => setRepoFilter(e.target.value)}
            >
              <option value="">All repositories</option>
              {repos.map((repo) => (
                <option key={repo} value={repo}>
                  {repoLabel(repo)}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <Panel title="New showdown" summary="Pick a feature and the agents to race on it">
        <RunLauncher
          features={features}
          agents={agents}
          disabled={preflight !== null && !preflight.live_runs_possible}
          onLaunched={() => {
            onLaunched();
            // A new run shows up on the board, so take them to it.
            setSection("contenders");
          }}
        />
      </Panel>

      {error && <Notice tone="danger">{error}</Notice>}

      <div className="section-bar">
        <Tabs
          group={GROUP}
          label="Results"
          className="subtabs"
          items={SECTIONS.map((value) => ({
            value,
            label: SECTION_LABELS[value],
            badge: badges[value],
          }))}
          value={section}
          onChange={setSection}
        />
        <span className="hint section-bar__count">{`${visible.length} job${visible.length === 1 ? "" : "s"}`}</span>
      </div>

      <SectionPanel section="contenders" current={section}>
        {visible.length === 0 ? (
          <div className="surface surface__body">
            <p className="hint">Nothing has run yet. Open New showdown above to start one.</p>
          </div>
        ) : (
          <>
            <GroupModePicker mode={mode} onChange={setMode} />
            <GroupedJobs
              jobs={visible}
              colorFor={colorFor}
              mode={mode}
              selectedName={selectedJob?.sandbox_name ?? null}
              onSelect={(job) => setSelectedName(job.sandbox_name)}
            />
          </>
        )}
      </SectionPanel>

      <SectionPanel section="runs" current={section}>
        <section className="surface" aria-label="Runs">
          {visible.length === 0 ? (
            <div className="surface__body">
              <p className="hint">No runs yet.</p>
            </div>
          ) : (
            <RunsTable
              jobs={visible}
              colorFor={colorFor}
              selectedName={selectedJob?.sandbox_name ?? null}
              onSelect={(job) => setSelectedName(job.sandbox_name)}
            />
          )}
        </section>
      </SectionPanel>

      <SectionPanel section="head-to-head" current={section}>
        <section className="surface surface__body" aria-label="Head to head">
          <ComparisonSpread jobs={visible} colorFor={colorFor} />
        </section>
      </SectionPanel>

      <SectionPanel section="approach" current={section}>
        <section className="surface surface__body" aria-label="How they worked">
          <ApproachComparison jobs={visible} colorFor={colorFor} />
        </section>
      </SectionPanel>

      <SectionPanel section="sandboxes" current={section}>
        <section className="surface surface__body" aria-label="Sandboxes">
          <SandboxControls sandboxes={sandboxes} onChanged={loadSandboxes} />
        </section>
      </SectionPanel>

      <Drawer
        open={selectedJob !== null}
        label={selectedJob ? `Inspect ${selectedJob.sandbox_name}` : "Inspect"}
        onClose={closeInspector}
      >
        {selectedJob && (
          <JobInspector
            job={selectedJob}
            color={colorFor(selectedJob)}
            position={{ index: selectedIndex + 1, total: ordered.length }}
            onPrevious={previous ? () => setSelectedName(previous.sandbox_name) : null}
            onNext={next ? () => setSelectedName(next.sandbox_name) : null}
            onClose={closeInspector}
          />
        )}
      </Drawer>
    </div>
  );
}
