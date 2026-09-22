/** The "point it at a repo and configure it" half of the app.
 *
 * A stack of collapsible panels, one per thing a showdown needs. Each panel
 * header says whether that part is ready, so a configured workspace reads
 * at a glance without opening anything.
 *
 * The editors render bare form bodies and report what they loaded through
 * callbacks; this view owns the panel chrome and the readiness summary.
 */

import { useState } from "react";
import type { AgentInfo, ConfigDoc, Feature, SecretList, WorkspaceList } from "../api";
import { ConfigEditor } from "../components/ConfigEditor";
import { FeatureEditor } from "../components/FeatureEditor";
import { IssueImporter } from "../components/IssueImporter";
import { SecretsManager } from "../components/SecretsManager";
import { WorkspacePicker } from "../components/WorkspacePicker";
import { Panel, type PanelStatus } from "../components/ui/Panel";
import { StatusChip } from "../components/ui/StatusChip";

function configStatus(doc: ConfigDoc | null): PanelStatus | undefined {
  if (doc === null) return undefined;
  if (!doc.editable) return { tone: "neutral", text: "read-only preset" };
  return doc.exists ? { tone: "ok", text: "saved" } : { tone: "warn", text: "not saved" };
}

function secretsStatus(list: SecretList | null): PanelStatus | undefined {
  if (list === null) return undefined;
  if (!list.available) return { tone: "warn", text: "sbx unavailable" };
  const n = list.secrets.length;
  return n === 0 ? { tone: "warn", text: "none stored" } : { tone: "ok", text: `${n} stored` };
}

export function SetupView({
  features,
  agents,
  workspaces,
  onChanged,
}: {
  features: Feature[];
  agents: AgentInfo[];
  workspaces: WorkspaceList;
  onChanged: () => void;
}) {
  const [reloadKey, setReloadKey] = useState(0);
  const [configDoc, setConfigDoc] = useState<ConfigDoc | null>(null);
  const [secrets, setSecrets] = useState<SecretList | null>(null);

  function reload() {
    setReloadKey((k) => k + 1);
    onChanged();
  }

  const active = workspaces.workspaces.find((w) => w.path === workspaces.active);
  const checks = [
    workspaces.active !== null,
    configDoc?.exists === true,
    features.length > 0,
  ];
  const ready = checks.filter(Boolean).length;

  return (
    <>
      <div className="view-head">
        <div>
          <h2>Setup</h2>
          <p>Pick the repository, set the rules, and define the tasks agents compete on.</p>
        </div>
        <StatusChip tone={ready === checks.length ? "ok" : "warn"}>
          {ready === checks.length ? "Ready to run" : `${ready} of ${checks.length} ready`}
        </StatusChip>
      </div>

      <div className="stack">
        <Panel
          step={1}
          title="Repository"
          summary={
            workspaces.active ? (
              <span className="mono">{workspaces.active}</span>
            ) : (
              "No repository selected"
            )
          }
          status={
            workspaces.active
              ? { tone: "ok", text: "selected" }
              : { tone: "warn", text: "required" }
          }
          defaultOpen={workspaces.active === null}
        >
          <WorkspacePicker onChanged={reload} />
        </Panel>

        <Panel
          step={2}
          title="Configuration"
          summary={configDoc ? <span className="mono">{configDoc.path}</span> : undefined}
          status={configStatus(configDoc)}
        >
          <ConfigEditor reloadKey={reloadKey} agents={agents} onDocChange={setConfigDoc} />
        </Panel>

        <Panel step={3} title="Secrets" status={secretsStatus(secrets)}>
          <SecretsManager onSecretsChange={setSecrets} />
        </Panel>

        <Panel
          step={4}
          title="Features"
          summary={features.map((f) => f.id).join(", ") || undefined}
          status={
            features.length > 0
              ? { tone: "ok", text: `${features.length} defined` }
              : { tone: "warn", text: "none yet" }
          }
        >
          {active?.github_repo && (
            <details className="disclosure panel__section">
              <summary>Import from GitHub issues</summary>
              <div className="disclosure__body">
                <IssueImporter repo={active.github_repo} onImported={reload} />
              </div>
            </details>
          )}
          <FeatureEditor features={features} agents={agents} onSaved={reload} />
        </Panel>
      </div>
    </>
  );
}
