import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchModelCatalogs,
  fetchModelRefresh,
  updateModels,
  type ModelCatalog,
  type ModelRefresh,
} from "../api";
import { modelSourceLabel } from "../lib/modelCatalog";
import { Notice } from "./ui/Notice";

export function ModelCatalogUpdater({
  disabled,
  onUpdated,
}: {
  disabled: boolean;
  onUpdated?: (() => void) | undefined;
}) {
  const [refresh, setRefresh] = useState<ModelRefresh | null>(null);
  const [catalogs, setCatalogs] = useState<Record<string, ModelCatalog>>({});
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const notify = useRef(onUpdated);
  const requested = useRef(false);

  // Kept current after each commit; it is only called from async callbacks,
  // which always run after the render that supplied it.
  useEffect(() => {
    notify.current = onUpdated;
  });

  useEffect(() => {
    let cancelled = false;
    fetchModelCatalogs().then((data) => {
      if (cancelled) return;
      setCatalogs(data.catalogs ?? {});

      if (!requested.current) setRefresh(data.refresh ?? null);
    }).catch(() => {
      if (!cancelled) setError("Could not load model update status.");
    });

    return () => { cancelled = true; };
  }, []);

  const refreshId = refresh?.state === "running" ? refresh.refresh_id : null;

  useEffect(() => {
    if (!refreshId) return;
    let cancelled = false;
    let pending = false;

    const timer = setInterval(() => {
      if (pending) return;
      pending = true;
      fetchModelRefresh(refreshId).then(async (next) => {
        if (cancelled) return;
        setError(null);

        if (next.state === "finished") {
          const data = await fetchModelCatalogs();

          if (cancelled) return;
          setCatalogs(data.catalogs ?? {});
          notify.current?.();
        }

        setRefresh(next);
      }).catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof ApiError ? cause.message : "Could not read model update progress.");

        if (cause instanceof ApiError && cause.status === 404) setRefresh(null);
      }).finally(() => { pending = false; });
    }, 1000);

    return () => { cancelled = true; clearInterval(timer); };
  }, [refreshId]);

  async function start() {
    requested.current = true;
    setStarting(true);
    setError(null);

    try {
      const next = await updateModels();
      setRefresh(next);

      if (next.state === "finished") notify.current?.();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not start the model update.");
    } finally {
      setStarting(false);
    }
  }

  const busy = starting || refreshId !== null;
  const lastUpdated = Object.values(catalogs).map((c) => c.updated_at).filter(Boolean).sort().at(-1);

  return (
    <div className="panel__section stack stack--tight">
      <div className="actions">
        <button type="button" disabled={disabled || busy} onClick={() => void start()}>
          Update models
        </button>
        {busy && <span role="status">Updating models… maximum 10 minutes.</span>}
      </div>
      <p className="hint">
        {lastUpdated
          ? <>Last updated: <time dateTime={lastUpdated}>{new Date(lastUpdated).toLocaleString()}</time>.</>
          : "Built-in suggestions. Models update only when you click Update models."}
      </p>
      {refresh && Object.keys(refresh.results).length > 0 && (
        <ul className="list" aria-label="Model update results">
          {Object.entries(refresh.results).map(([provider, result]) => (
            <li key={provider}>
              <strong>{provider}</strong>: {result.status === "updated"
                ? `${result.count} models — ${modelSourceLabel(result.source).toLowerCase()}`
                : result.detail}
            </li>
          ))}
        </ul>
      )}
      {refresh?.error && <Notice tone="danger">{refresh.error}</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}
    </div>
  );
}
