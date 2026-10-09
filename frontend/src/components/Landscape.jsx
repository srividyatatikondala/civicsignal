import Icon from "./Icons.jsx";
// "Search result count is not the same as independent evidence" — shown as a funnel,
// with the original search and the targeted follow-up searches kept apart.

const LEVELS = ["high", "medium", "low", "off_topic"];

function RelevanceBar({ levels, label }) {
  const total = LEVELS.reduce((a, k) => a + (levels[k] || 0), 0) || 1;
  return (
    <>
      <div className="bar" role="img" aria-label={label}>
        {LEVELS.map((k) =>
          levels[k] ? (
            <span key={k} className={`bar-seg rel-${k}`} style={{ width: `${(100 * levels[k]) / total}%` }}
                  title={`${k.replace("_", " ")}: ${levels[k]}`} />
          ) : null
        )}
      </div>
      <ul className="legend">
        {LEVELS.map((k) => (
          <li key={k}><span className={`dot rel-${k}`} /> {k.replace("_", " ")} {levels[k] || 0}</li>
        ))}
      </ul>
    </>
  );
}

export function Independence({ landscape: l, repeated }) {
  const steps = [
    { n: l.result_appearances, label: "result appearances" },
    { n: l.unique_urls, label: "unique URLs" },
    { n: l.unique_sources, label: "distinct pages" },
    { n: l.unique_domains, label: "domains" },
    ...(l.independent_source_groups != null
      ? [{ n: l.independent_source_groups, label: "apparently independent groups" }]
      : []),
  ];
  const types = Object.entries(l.source_types || {}).sort((a, b) => b[1] - a[1]);
  return (
    <section className="card">
      <div className="card-head">
        <h2><span className="h-icon"><Icon name="network" /></span>Source independence</h2>
        <span className="muted small">
          Result count is not the same as independent evidence
          {l.includes_follow_ups ? " · totals include targeted follow-up searches" : ""}
        </span>
      </div>
      <ol className="funnel">
        {steps.map((s, i) => (
          <li key={s.label}>
            <span className="funnel-n">{s.n}</span>
            <span className="funnel-label">{s.label}</span>
            {i < steps.length - 1 && <span className="funnel-arrow" aria-hidden="true"><Icon name="chevron" size={18} /></span>}
          </li>
        ))}
      </ol>
      <p className="small">
        {repeated
          ? <><Icon name="copy" size={14} /> <strong>{repeated}</strong> potentially repeated or syndicated content finding{repeated === 1 ? "" : "s"} (see Potential issues).</>
          : <span className="muted">No potentially repeated or syndicated content was detected.</span>}
      </p>
      <h3>Source types</h3>
      <ul className="pill-list">
        {types.map(([t, n]) => (
          <li key={t} className={`pill type-${t}`}>{t.replaceAll("_", " ")} · {n}</li>
        ))}
      </ul>
      <p className="muted small">Source type describes who published a page; it is not a trust score.</p>
    </section>
  );
}

export default function Landscape({ landscape: l }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2><span className="h-icon"><Icon name="layers" /></span>Search landscape</h2>
        <span className="muted small">The original search and the targeted searches are measured separately</span>
      </div>
      <div className="landscape-grid">
        <div>
          <h3><Icon name="search" size={14} /> Original search</h3>
          <p><strong>{l.base_results}</strong> results returned for your question</p>
          <RelevanceBar levels={l.base_relevance || l.relevance || {}} label="Relevance of the original search" />
        </div>
        {l.includes_follow_ups && (
          <div>
            <h3><Icon name="route" size={14} /> Targeted investigation searches</h3>
            <p>
              <strong>{l.follow_up_results}</strong> additional results ·{" "}
              <strong>{l.follow_up_new_sources}</strong> new pages ·{" "}
              <strong>{l.follow_up_relevant_results}</strong> relevant
            </p>
            <RelevanceBar levels={l.follow_up_relevance || {}} label="Relevance of the follow-up searches" />
          </div>
        )}
        <div>
          <h3><Icon name="landmark" size={14} /> Official sources</h3>
          <p><strong>{l.official_pages_found ?? "—"}</strong> official pages found</p>
          <p><strong>{l.official_topic_sources ?? "—"}</strong> that address the topic</p>
          <p className="muted small">Which claim values an official source states is shown in the evidence map.</p>
        </div>
        <div>
          <h3><Icon name="file" size={14} /> Claims</h3>
          <p><strong>{l.claims_extracted}</strong> extracted · <strong>{l.claims_compared}</strong> compared for conflicts</p>
        </div>
      </div>
    </section>
  );
}
