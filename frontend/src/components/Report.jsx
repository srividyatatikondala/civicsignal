import Landscape, { Independence } from "./Landscape.jsx";
import IssueList from "./IssueList.jsx";
import EvidenceMap from "./EvidenceMap.jsx";
import FollowUps, { Trail } from "./FollowUps.jsx";
import Icon from "./Icons.jsx";
import { readable, STATUS_ICON, STATUS_TONE } from "../format.js";

function Section({ icon, title, note, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      <div className="card-head">
        <h2><span className="h-icon"><Icon name={icon} /></span>{title}</h2>
        {note && <span className="muted small">{note}</span>}
      </div>
      {children}
    </section>
  );
}

// Key findings: counts read straight from the report — nothing is recomputed or scored.
function KeyFindings({ report }) {
  const values = report.evidence_map.flatMap((m) => m.values);
  const conflicts = report.evidence_map.filter((m) => m.has_conflict).length;
  const stale = values.filter((v) => v.potentially_stale).length;
  const supported = values.filter((v) => v.stated_by_official_source).length;
  const l = report.landscape;
  const tiles = [
    { icon: "conflict", tone: conflicts ? "danger" : "ok", n: conflicts, label: conflicts === 1 ? "conflicting claim" : "conflicting claims" },
    { icon: "clock", tone: stale ? "warn" : "ok", n: stale, label: stale === 1 ? "potentially stale value" : "potentially stale values" },
    { icon: "landmark", tone: supported ? "ok" : "muted", n: `${supported}/${values.length}`, label: "values stated by an official source" },
    { icon: "network", tone: "info", n: l.independent_source_groups ?? "—", label: `apparently independent groups (of ${l.result_appearances} results)` },
    { icon: "route", tone: "info", n: report.follow_ups.length, label: report.follow_ups.length === 1 ? "targeted follow-up search" : "targeted follow-up searches" },
  ];
  return (
    <div className="kpis">
      {tiles.map((t) => (
        <div key={t.label} className={`kpi tone-${t.tone}`}>
          <span className="kpi-icon"><Icon name={t.icon} size={20} /></span>
          <span className="kpi-n">{t.n}</span>
          <span className="kpi-label">{t.label}</span>
        </div>
      ))}
    </div>
  );
}

export default function Report({ report }) {
  const code = report.status.code;
  const tone = STATUS_TONE[code] || "muted";
  return (
    <main className="report">
      <section className={`status tone-${tone}`}>
        <div className="status-top">
          <span className="eyebrow"><Icon name="search" size={14} /> Investigation</span>
          <span className={`badge badge-${report.data_notice.mode}`} title={report.data_notice.detail}>
            {report.data_notice.label}
          </span>
        </div>
        <h2 className="question">{report.question}</h2>
        <div className="status-line">
          <span className={`status-chip tone-${tone}`}>
            <Icon name={STATUS_ICON[code] || "info"} size={18} /> {report.status.label}
          </span>
        </div>
        <p className="status-explain">{readable(report.status.explanation)}</p>
        <KeyFindings report={report} />
        <p className="muted small status-foot">
          <Icon name="info" size={14} /> Analysed as of {readable(report.as_of_date)} · {readable(report.data_notice.detail)}
        </p>
      </section>

      <Landscape landscape={report.landscape} />

      <Section icon="alert" title="Potential issues" note={<IssueCounts counts={report.issue_counts} />}>
        <IssueList issues={report.issues} />
      </Section>

      <Section icon="map" title="Evidence map" note="Who states what — counts are context, not votes" className="card-feature">
        <EvidenceMap entries={report.evidence_map} excluded={report.excluded_claims} />
      </Section>

      <Independence landscape={report.landscape} repeated={report.issue_counts.repeated_content || 0} />

      <Section icon="route" title="Investigation trail" note="What CivicSignal did, step by step">
        <Trail steps={report.trail || []} />
        {report.follow_ups.length > 0 && (
          <details className="more">
            <summary><Icon name="chevron" size={14} className="chev" /> Follow-up search details ({report.follow_ups.length})</summary>
            <FollowUps followUps={report.follow_ups} />
          </details>
        )}
      </Section>

      <Section icon="landmark" title="Where to verify" className="verify">
        <p className="muted">{report.where_to_verify_note}</p>
        {report.where_to_verify.length > 0 && (
          <ul className="verify-grid">
            {report.where_to_verify.map((v, i) => (
              <li key={v.domain + v.title + i} className="verify-card">
                <div className="verify-top">
                  <span className="verify-icon"><Icon name="landmark" size={18} /></span>
                  <div className="verify-name">
                    {v.url ? (
                      <a href={v.url} target="_blank" rel="noopener noreferrer">
                        {v.title || v.domain} <Icon name="external" size={13} />
                      </a>
                    ) : (v.title || v.domain)}
                    <span className="muted small">{v.domain}</span>
                  </div>
                </div>
                {v.states_values.length > 0 ? (
                  <p className="verify-states"><Icon name="check" size={14} /> States: <strong>{v.states_values.join("; ")}</strong></p>
                ) : (
                  <p className="verify-states muted">Addresses the topic; does not state any of the compared values.</p>
                )}
                {v.found_by_label && <p className="muted small">Found by the {v.found_by_label}</p>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section icon="book" title="How to read this" className="notes">
        <ul>
          {report.method_notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
        <details>
          <summary>Checks run</summary>
          <ul className="checks">
            {Object.entries(report.checks_run).map(([k, v]) => (
              <li key={k}><code>{k}</code> — {v.replaceAll("_", " ")}</li>
            ))}
          </ul>
          {report.warnings.length > 0 && (
            <>
              <h3>Notes</h3>
              <ul>{report.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </>
          )}
        </details>
      </Section>
    </main>
  );
}

function IssueCounts({ counts }) {
  const labels = {
    conflicting_claims: "conflicts",
    staleness: "potentially stale",
    primary_source: "primary-source",
    repeated_content: "possibly repeated or syndicated",
    relevance: "relevance",
  };
  const entries = Object.entries(counts);
  if (entries.length === 0) return null;
  return (
    <span className="counts">
      {entries.map(([k, n]) => (
        <span key={k} className={`count count-${k}`}>{n} {labels[k] || k}</span>
      ))}
    </span>
  );
}
