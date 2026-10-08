import { useEffect, useState } from "react";
import { getDemoQueries, getHealth, getReport, investigate, listInvestigations } from "./api.js";
import Report from "./components/Report.jsx";
import Icon, { HeroArt, Logo } from "./components/Icons.jsx";
import { STATUS_SHORT, STATUS_TONE } from "./format.js";

const STEPS = [
  ["search", "Search", "Runs your question through Google and Google News via SerpApi."],
  ["layers", "Analyse", "Reads every result as evidence: dates, amounts, sources, relevance."],
  ["route", "Investigate", "Runs targeted follow-up searches only where a gap is found."],
  ["map", "Map the evidence", "Shows who states what, what conflicts, and where to verify."],
];

export default function App() {
  const [query, setQuery] = useState("");
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [health, setHealth] = useState(null);
  const [demo, setDemo] = useState([]);
  const [recent, setRecent] = useState([]);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
    getDemoQueries().then((d) => setDemo(d.queries || [])).catch(() => setDemo([]));
    refreshRecent();
  }, []);

  function refreshRecent() {
    listInvestigations()
      .then((rows) => setRecent(rows.filter((r, i) => rows.findIndex((x) => x.query === r.query) === i)))
      .catch(() => setRecent([]));
  }

  async function run(q) {
    const text = (q ?? query).trim();
    if (text.length < 3) {
      setError("Enter a question of at least 3 characters.");
      return;
    }
    setQuery(text);
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      setReport(await investigate(text));
      refreshRecent();
    } catch (e) {
      setError(e.message || "The investigation could not be completed.");
    } finally {
      setLoading(false);
    }
  }

  async function open(id) {
    setLoading(true);
    setError(null);
    try {
      const r = await getReport(id);
      setReport(r);
      setQuery(r.question);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  const offline = health === null;
  return (
    <div className="page">
      <header className="masthead">
        <button className="brand" onClick={() => { setReport(null); setError(null); }} title="Home">
          <Logo size={40} />
          <div>
            <h1>CivicSignal</h1>
            <p className="tagline">Search Information Integrity Analyzer</p>
          </div>
        </button>
        <div className="mode">
          {offline ? (
            <span className="badge badge-warn">Backend not reachable</span>
          ) : health.serpapi.mock_mode ? (
            <span className="badge badge-demo"><Icon name="database" size={13} /> Captured SerpApi data · not a live search</span>
          ) : (
            <span className="badge badge-live"><span className="live-dot" /> LIVE · SerpApi</span>
          )}
        </div>
      </header>

      <section className={`search-panel ${report || loading ? "compact" : ""}`}>
        {!report && !loading && (
          <div className="hero">
            <div className="hero-text">
              <span className="hero-kicker"><Icon name="sparkle" size={14} /> Track 05 · Knowledge &amp; Public Interest</span>
              <h2 className="hero-title">See what search results <span className="grad">really</span> say before you act on them.</h2>
              <p className="lede">
                CivicSignal does not answer your question. It investigates the search results about it — stale dates,
                conflicting claims, repeated content, and whether an official source actually states the value.
              </p>
            </div>
            <HeroArt />
          </div>
        )}
        <form
          className="search"
          onSubmit={(e) => {
            e.preventDefault();
            run();
          }}
        >
          <label htmlFor="q" className="visually-hidden">What do you want to investigate?</label>
          <Icon name="search" size={20} className="search-icon" />
          <input
            id="q"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="What do you want to investigate? e.g. PM Kisan next installment date 2026"
            maxLength={300}
            disabled={loading}
          />
          <button type="submit" disabled={loading}>{loading ? "Investigating…" : "Investigate"}</button>
        </form>
        {demo.length > 0 && (
          <div className="chips" aria-label="Demo questions">
            <span className="chips-label">Try a demo question:</span>
            {demo.map((d) => (
              <button key={d.query} className="chip" onClick={() => run(d.query)} disabled={loading}>
                {d.query}
              </button>
            ))}
          </div>
        )}
      </section>

      {loading && (
        <div className="loading" role="status">
          <div className="spinner" aria-hidden="true" />
          <div>
            <strong>Investigating…</strong>
            <p>Searching through SerpApi, analysing every result as evidence, and running follow-up searches only where the evidence calls for them.</p>
          </div>
        </div>
      )}
      {error && <div className="error" role="alert">{error}</div>}

      {report && !loading && <Report report={report} />}

      {!report && !loading && (
        <section className="how" aria-labelledby="how-title">
          <h2 id="how-title" className="how-title">How CivicSignal works</h2>
          <ol className="steps">
            {STEPS.map(([icon, title, text], i) => (
              <li key={title} className="step">
                <span className="step-icon"><Icon name={icon} size={22} /></span>
                <h3><span className="step-n">{i + 1}</span> {title}</h3>
                <p>{text}</p>
                {i < STEPS.length - 1 && <span className="step-arrow" aria-hidden="true"><Icon name="chevron" size={20} /></span>}
              </li>
            ))}
          </ol>
        </section>
      )}

      {!report && !loading && recent.length > 0 && (
        <section className="card recent">
          <div className="card-head">
            <h2><Icon name="clock" /> Recent investigations</h2>
          </div>
          <ul>
            {recent.map((r) => (
              <li key={r.id}>
                <button className="recent-row" onClick={() => open(r.id)}>
                  <span className="recent-q">{r.query}</span>
                  <span className={`status-pill tone-${STATUS_TONE[r.integrity_status] || "muted"}`}>
                    {STATUS_SHORT[r.integrity_status] || r.integrity_status}
                  </span>
                  <span className="muted small">{r.unique_sources} sources</span>
                  <Icon name="chevron" size={16} className="muted" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="footer">
        CivicSignal analyses search results as evidence. It does not decide what is true — always confirm
        time-sensitive information with the official source.
      </footer>
    </div>
  );
}
