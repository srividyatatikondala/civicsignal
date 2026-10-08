import { useState } from "react";
import Icon from "./Icons.jsx";
import { readable } from "../format.js";

const CAT_ICON = {
  conflicting_claims: "conflict",
  staleness: "clock",
  primary_source: "landmark",
  repeated_content: "copy",
  relevance: "target",
};

const FOUND_BY = {
  base_query: "original search",
  primary_source_lookup: "official-source lookup",
  recency_contrast: "past-month search",
  news_check: "news search",
  regional_compare: "language comparison",
};

export default function IssueList({ issues }) {
  if (issues.length === 0) {
    return <p className="empty"><Icon name="check" /> No potential issues were detected by the checks that ran. This is not a verification of the information.</p>;
  }
  return (
    <ul className="issues">
      {issues.map((card) => <IssueCard key={card.finding_id} card={card} defaultOpen={false} />)}
    </ul>
  );
}

function IssueCard({ card, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <li className={`issue issue-${card.category}`}>
      <button className="issue-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="issue-icon"><Icon name={CAT_ICON[card.category] || "alert"} size={18} /></span>
        <span className="issue-title">{readable(card.title)}</span>
        <span className="issue-cat">{card.category_label}</span>
        <span className={`sev sev-${card.severity}`}>{card.severity}</span>
        <Icon name="chevron" size={16} className={`chev ${open ? "open" : ""}`} />
      </button>
      {open && (
        <div className="issue-body">
          <p>{readable(card.summary)}</p>
          {card.evidence.length > 0 && (
            <>
              <h4>Evidence ({card.evidence.length})</h4>
              <ul className="evidence">
                {card.evidence.map((e, i) => <EvidenceRow key={i} e={e} />)}
              </ul>
            </>
          )}
          {card.how_to_verify && <p className="hint"><Icon name="info" size={14} /> How to verify: {readable(card.how_to_verify)}</p>}
        </div>
      )}
    </li>
  );
}

function EvidenceRow({ e }) {
  return (
    <li className="evidence-row">
      {e.quote && <blockquote><Icon name="quote" size={14} className="q-icon" />{e.quote}</blockquote>}
      <div className="evidence-meta">
        {e.source_type && <span className={`pill type-${e.source_type}`}>{e.source_type.replaceAll("_", " ")}</span>}
        {e.url ? (
          <a href={e.url} target="_blank" rel="noopener noreferrer">{e.domain || e.url}</a>
        ) : (
          <span>{e.domain || "no URL"}</span>
        )}
        {e.search_position != null && <span className="muted">result #{e.search_position}</span>}
        <span className="muted">via {FOUND_BY[e.found_by] || e.found_by}</span>
        {e.field && <span className="muted">· {e.field}</span>}
      </div>
      {!e.quote && e.title && <div className="evidence-title">{e.title}</div>}
    </li>
  );
}
