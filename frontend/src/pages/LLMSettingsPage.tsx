import React, { useState, useEffect, useCallback } from "react";
import { llmSettingsApi } from "../api/client";
import type { LLMSettingsConfig, LLMSettingsResponse } from "../types";
import { Button, Banner, Skeleton } from "../components/ui";

// Provider-prefixed model fields grouped by tier. The prefix (before the ":")
// decides which provider, endpoint, and API key each call uses — switching
// provider is just editing these strings (+ having the key set in .env).
const MODEL_FIELDS: {
  key: keyof LLMSettingsConfig;
  label: string;
  hint: string;
  tier: "frontier" | "fast" | "embed";
}[] = [
  { key: "llm_model", label: "Orchestrator / designers", hint: "Main deepagents model", tier: "frontier" },
  { key: "sql_generator_model", label: "SQL generator", hint: "Reasoning-heavy subagent", tier: "frontier" },
  { key: "dashboard_widget_sql_model", label: "Dashboard / report SQL", hint: "Batched widget & sheet SQL, repair", tier: "frontier" },
  { key: "schema_analyst_model", label: "Schema analyst", hint: "Cheap tier — mostly tool calls", tier: "fast" },
  { key: "fast_path_model", label: "Fast path", hint: "Single-shot cheap attempt", tier: "fast" },
  { key: "embedding_model", label: "Embeddings", hint: "RAG vectors — may be a different provider (Anthropic has none)", tier: "embed" },
];

const RPM_FIELDS: { key: keyof LLMSettingsConfig; label: string; hint: string }[] = [
  { key: "llm_frontier_rpm", label: "Frontier RPM", hint: "Orchestrator, SQL gen, dashboard/report SQL" },
  { key: "llm_fast_rpm", label: "Fast RPM", hint: "Fast path + schema analyst" },
  { key: "llm_embed_rpm", label: "Embeddings RPM", hint: "Embedding calls" },
];

export default function LLMSettingsPage() {
  const [config, setConfig] = useState<LLMSettingsConfig | null>(null);
  const [keysPresent, setKeysPresent] = useState<Record<string, boolean>>({});
  const [providers, setProviders] = useState<string[]>([]);
  const [version, setVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const applyResponse = (data: LLMSettingsResponse) => {
    setConfig(data.config);
    setKeysPresent(data.provider_keys_present ?? {});
    if (data.known_providers?.length) setProviders(data.known_providers);
    setVersion(data.version ?? null);
  };

  const load = useCallback(async () => {
    try {
      setLoading(true);
      applyResponse(await llmSettingsApi.get());
    } catch (e) {
      setError("Failed to load LLM settings (is the AI engine reachable?)");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const set = <K extends keyof LLMSettingsConfig>(key: K, value: LLMSettingsConfig[K]) => {
    setConfig((c) => (c ? { ...c, [key]: value } : c));
    setSuccess(null);
  };

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const data = await llmSettingsApi.update(config);
      applyResponse(data);
      setSuccess(`Saved — applied live (version ${data.version}). No restart needed.`);
    } catch (e: any) {
      // AI-engine validation → {detail}; core-api error → {error, details}.
      const body = e?.response?.data ?? {};
      setError(body.detail || body.details || body.error || "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="llm-page">
        <Skeleton height={28} width="240px" />
        <div style={{ marginTop: 20 }}>
          <Skeleton height={16} />
        </div>
        <div style={{ marginTop: 12 }}>
          <Skeleton height={16} width="70%" />
        </div>
      </div>
    );
  }
  if (!config) {
    return (
      <div className="llm-page">
        <div className="ds-error">
          <span>⚠️ {error ?? "No settings available."}</span>
        </div>
        <button className="btn btn-secondary" onClick={load}>Retry</button>
      </div>
    );
  }

  return (
    <div className="llm-page">
      <div className="llm-header">
        <h1 className="llm-title">⚙️ LLM Settings</h1>
        {version != null && <span className="llm-version">config v{version}</span>}
      </div>
      <p className="llm-subtitle">
        Switch provider by editing the <code>provider:model</code> strings below. The prefix
        (<code>{providers.join(", ") || "openai, anthropic, google_genai, openai_compat, ollama"}</code>)
        selects the endpoint and API key. Changes apply live — no restart.
      </p>

      {error && (
        <Banner kind="error" message={error} onDismiss={() => setError(null)} />
      )}
      {success && (
        <Banner kind="success" message={success} onDismiss={() => setSuccess(null)} />
      )}

      {/* Provider API keys — read-only; set in .env, never here */}
      <div className="llm-section">
        <div className="llm-section-title">Provider API keys</div>
        <div className="llm-section-hint">Keys are set in the environment (.env), never in the UI. This just shows what's configured.</div>
        <div className="llm-key-row">
          {Object.entries(keysPresent).map(([name, present]) => (
            <span key={name} className={`llm-key-pill ${present ? "present" : ""}`}>
              {present ? "✓" : "✗"} {name}
            </span>
          ))}
        </div>
      </div>

      {/* Model strings */}
      <div className="llm-section">
        <div className="llm-section-title">Models</div>
        <div className="llm-section-hint">Provider-prefixed, e.g. <code>openai:gpt-4o</code>, <code>anthropic:claude-sonnet-5</code>.</div>
        {MODEL_FIELDS.map((f) => (
          <div key={f.key} className="llm-field">
            <label htmlFor={f.key}>
              {f.label} <span className="llm-field-tier">· {f.tier} tier</span>
            </label>
            <div className="llm-section-hint">{f.hint}</div>
            <input
              id={f.key}
              className="form-input"
              value={String(config[f.key] ?? "")}
              onChange={(e) => set(f.key, e.target.value as never)}
            />
          </div>
        ))}
      </div>

      {/* Fast path */}
      <div className="llm-section">
        <div className="llm-section-title">Fast path</div>
        <div className="llm-section-hint">A cheap single-shot attempt before the full pipeline.</div>
        <label className="llm-checkbox-row">
          <input
            type="checkbox"
            checked={config.fast_path_enabled}
            onChange={(e) => set("fast_path_enabled", e.target.checked)}
          />
          Enable fast path
        </label>
        <div className="llm-field">
          <label htmlFor="threshold">Confidence threshold (0–1)</label>
          <div className="llm-section-hint">Fast-path plans below this escalate to the full pipeline.</div>
          <input
            id="threshold"
            type="number"
            min={0}
            max={1}
            step={0.05}
            className="form-input"
            style={{ width: 140 }}
            value={config.fast_path_confidence_threshold}
            onChange={(e) => set("fast_path_confidence_threshold", parseFloat(e.target.value))}
          />
        </div>
      </div>

      {/* Rate limits */}
      <div className="llm-section">
        <div className="llm-section-title">Rate limits (requests / minute, 0 = unlimited)</div>
        <div className="llm-section-hint">
          Per-tier caps across both LLM paths. Tiers sharing one provider key should sum under that provider's quota.
        </div>
        <div className="llm-rpm-grid">
          {RPM_FIELDS.map((f) => (
            <div key={f.key} className="llm-rpm-field">
              <label htmlFor={f.key}>{f.label}</label>
              <div className="llm-section-hint">{f.hint}</div>
              <input
                id={f.key}
                type="number"
                min={0}
                step={1}
                className="form-input"
                value={Number(config[f.key] ?? 0)}
                onChange={(e) => set(f.key, Math.max(0, parseInt(e.target.value || "0", 10)) as never)}
              />
            </div>
          ))}
        </div>
      </div>

      {/* Local / self-hosted */}
      <div className="llm-section">
        <div className="llm-section-title">Local / self-hosted endpoint</div>
        <div className="llm-section-hint">
          Base URL for the <code>openai_compat:</code> / <code>ollama:</code> prefixes only (vLLM, LM Studio, a
          gateway…). Leave blank for cloud providers.
        </div>
        <input
          className="form-input"
          placeholder="http://host:port/v1"
          value={config.llm_base_url ?? ""}
          onChange={(e) => set("llm_base_url", e.target.value)}
        />
      </div>

      <div className="llm-actions">
        <Button variant="primary" onClick={handleSave} busy={saving} busyLabel="Saving…">
          Save &amp; apply
        </Button>
        <Button variant="secondary" onClick={load} disabled={saving}>
          Reset
        </Button>
      </div>
    </div>
  );
}
