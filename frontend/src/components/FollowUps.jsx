import Icon from "./Icons.jsx";
import { readable } from "../format.js";

// Outcome wording comes from the backend (outcome_label); only the badge colour is chosen here.
const TONE = {
  support_found: "ok",
  official_found_not_addressing_claims: "info",
  additional_relevant_evidence: "info",
  newer_relevant_evidence: "info",
  failed: "warn",
};

const KIND_ICON = { search: "search", gap: "alert", action: "route", outcome: "check", result: "target" };
const KIND_LABEL = { search: "Searched", gap: "Gap found", action: "Searched again", outcome: "Outcome", result: "Result" };

export function Trail({ steps }) {
  return (
    <ol className="timeline">
      {steps.map((s, i) => (
        <li key={i} className={`tl-step tl-${s.kind}`}>
          <span className="tl-dot"><Icon name={KIND_ICON[s.kind] || "info"} size={14} /></span>
          <div className="tl-body">
            <span className="tl-kind">{KIND_LABEL[s.kind] || s.kind}</span>
            <p className="tl-text" title={readable(s.text)}>{readable(s.text)}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function FollowUps({ followUps }) {
  return (
    <ol className="followups">
      {followUps.map((f, i) => (
        <li key={i} className="followup">
          <div className="followup-head">
            <span className="badge badge-info">{f.reason_label}</span>
            <code className="query">
              {f.engine !== "google" ? `[${f.engine}] ` : ""}
              {f.query}
              {f.params?.tbs ? ` · past month` : ""}
              {f.params?.hl && f.reason === "regional_compare" ? ` · hl=${f.params.hl}` : ""}
            </code>
          </div>
          <p className="followup-q"><strong>Question:</strong> {readable(f.question)}</p>
          <div className="followup-why">
            <strong>Why this search was needed:</strong>
            <ul>{f.triggered_by.map((t) => <li key={t}>{readable(t)}</li>)}</ul>
          </div>
          <p className="followup-outcome">
            {f.outcome_label && (
              <span className={`badge badge-${TONE[f.outcome_code] || "neutral"}`}>{f.outcome_label}</span>
            )}{" "}
            <strong>Outcome:</strong> {readable(f.outcome)}
          </p>
        </li>
      ))}
    </ol>
  );
}
