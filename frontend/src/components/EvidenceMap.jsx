import Icon from "./Icons.jsx";
import { readable } from "../format.js";

const EXCLUDED_LABELS = {
  not_comparable_attribute: "historical, publication or unspecified dates and totals",
  subject_unresolved: "subject could not be pinned down",
  no_source: "no usable URL",
  procurement_notice: "tender / RFP bid dates",
  result_weakly_relevant: "from weakly related results",
  result_off_topic: "from off-topic results",
  relevance_unknown: "relevance not assessed",
};

function SourceList({ sources }) {
  if (sources.length === 0) return <span className="muted">none</span>;
  return (
    <ul className="src-list">
      {sources.map((s) => (
        <li key={s.source_id} title={s.title || undefined}>
          {s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{s.domain}</a> : s.domain}
          <span className={`pill type-${s.source_type}`}>{s.source_type.replaceAll("_", " ")}</span>
        </li>
      ))}
    </ul>
  );
}

function MapValueCard({ v }) {
  return (
    <div className={`map-value ${v.potentially_stale ? "map-value-stale" : ""}`}>
      <div className="map-value-head">
        <span className="value-icon"><Icon name={v.value_type === "amount" ? "file" : "clock"} size={16} /></span>
        <span className="value" title={v.value}>{v.value_label || v.value}</span>
        <span className="muted small">
          {v.value_type}
          {v.temporal_roles.length > 0 && ` · ${v.temporal_roles.join(", ")}`}
          {v.temporal_status && ` · ${v.temporal_status}`}
        </span>
        {v.potentially_stale && <span className="badge badge-stale"><Icon name="clock" size={12} /> potentially stale</span>}
        <span className={`support-chip ${v.stated_by_official_source ? "yes" : "no"}`}>
          <Icon name={v.stated_by_official_source ? "check" : "landmark"} size={13} />
          {v.stated_by_official_source ? "Official source states this" : "No official source states this"}
        </span>
      </div>

      <div className="map-value-grid">
        <div>
          <h4>Official support</h4>
          <p className="small">{v.official_support_reason}</p>
          {v.official_sources.length > 0 && <SourceList sources={v.official_sources} />}
        </div>
        <div>
          <h4>Other sources stating it</h4>
          <SourceList sources={v.other_sources} />
        </div>
        <div>
          <h4>Independence</h4>
          <p><span className="big-n">{v.independent_groups ?? "—"}</span> apparently independent group{v.independent_groups === 1 ? "" : "s"} among {v.sources.length} source{v.sources.length === 1 ? "" : "s"}</p>
        </div>
      </div>

      {v.evidence.length > 0 && (
        <details className="map-evidence" open={v.evidence.length <= 2}>
          <summary><Icon name="quote" size={13} /> Evidence ({v.evidence.length})</summary>
          <ul>
            {v.evidence.map((e) => (
              <li key={e.evidence_span_id}>
                <blockquote>“{e.excerpt}{e.truncated ? " …" : ""}”</blockquote>
                <span className="muted small">
                  {e.domain} · {e.field} · found by the {e.found_by_label}
                  <span className="trace" title={`Trace — claim ${e.claim_id} · result ${e.result_id} · evidence ${e.evidence_span_id} · search ${e.search_run_id}`}>
                    {" "}<Icon name="info" size={12} />
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export default function EvidenceMap({ entries, excluded }) {
  const excludedEntries = Object.entries(excluded || {});
  return (
    <>
      {entries.length === 0 ? (
        <p className="empty"><Icon name="info" /> No comparable claims (dates, deadlines, fees, amounts) were found to compare across sources.</p>
      ) : (
        <div className="map">
          {entries.map((m) => (
            <div key={m.cluster_id} className={`map-entry ${m.has_conflict ? "map-conflict" : ""}`}>
              <div className="map-title">
                <span className="h-icon"><Icon name={m.has_conflict ? "conflict" : "check"} size={16} /></span>
                <strong>{m.attribute}</strong> <span className="muted">of {m.subject}</span>
                {m.has_conflict ? (
                  <span className="badge badge-danger">conflicting values</span>
                ) : (
                  <span className="badge badge-neutral">no conflict</span>
                )}
              </div>
              {m.conflict_explanation && <p className="conflict-note"><Icon name="alert" size={15} /> {readable(m.conflict_explanation)}</p>}
              {m.values.map((v) => <MapValueCard key={v.value} v={v} />)}
            </div>
          ))}
        </div>
      )}
      {excludedEntries.length > 0 && (
        <details className="excluded">
          <summary>Claims kept as evidence but not compared ({excludedEntries.reduce((a, [, n]) => a + n, 0)})</summary>
          <ul>
            {excludedEntries.map(([k, n]) => <li key={k}>{n} — {EXCLUDED_LABELS[k] || k.replaceAll("_", " ")}</li>)}
          </ul>
        </details>
      )}
    </>
  );
}
