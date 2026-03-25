import { FormEvent, useEffect, useState } from "react";
import { DesktopAppProps } from "../registry";
import "./styles.css";

interface CompanySearchFilters {
  industry?: string | null;
  country?: string | null;
  min_employees?: number | null;
  max_employees?: number | null;
  min_revenue?: number | null;
  max_revenue?: number | null;
  is_verified?: boolean | null;
  search_query?: string | null;
}

interface CompanySearchRequest {
  filters?: CompanySearchFilters | null;
  page: number;
  page_size: number;
}

interface CompanySummary {
  id: string;
  name: string;
  domain?: string | null;
  industry?: string | null;
  country?: string | null;
  description?: string | null;
  employee_count?: number | null;
  revenue?: number | null;
  founded_year?: number | null;
  website?: string | null;
  linkedin_url?: string | null;
  is_verified: boolean;
  confidence_score?: number | null;
  last_updated?: string | null;
  created_at?: string | null;
  metadata?: Record<string, unknown> | null;
}

interface CompanySearchResponse {
  companies: CompanySummary[];
  total: number;
  page: number;
  page_size: number;
}

interface CompanyStatistics {
  total_companies: number;
  verified_companies: number;
  industries_count: number;
  countries_count: number;
  avg_confidence_score: number;
  recent_discoveries: number;
}

interface SearchFormState {
  query: string;
  industry: string;
  country: string;
}

const initialFormState: SearchFormState = {
  query: "",
  industry: "",
  country: "",
};

const emptyStats: CompanyStatistics = {
  total_companies: 0,
  verified_companies: 0,
  industries_count: 0,
  countries_count: 0,
  avg_confidence_score: 0,
  recent_discoveries: 0,
};

function compactText(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function buildPayload(form: SearchFormState): CompanySearchRequest {
  return {
    filters: {
      search_query: compactText(form.query),
      industry: compactText(form.industry),
      country: compactText(form.country),
    },
    page: 1,
    page_size: 25,
  };
}

function formatMaybeNumber(value?: number | null): string {
  if (value === null || value === undefined) {
    return "Unknown";
  }

  return new Intl.NumberFormat("en-US").format(value);
}

function formatConfidence(value?: number | null): string {
  if (value === null || value === undefined) {
    return "No score";
  }

  return `${Math.round(value * 100)}%`;
}

function formatUpdated(value?: string | null): string {
  if (!value) {
    return "Not yet collected";
  }

  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) {
    return value;
  }

  return new Date(parsed).toLocaleString();
}

export default function CompetitiveIntelligenceApp({ app }: DesktopAppProps) {
  const [stats, setStats] = useState<CompanyStatistics>(emptyStats);
  const [form, setForm] = useState<SearchFormState>(initialFormState);
  const [results, setResults] = useState<CompanySearchResponse>({
    companies: [],
    total: 0,
    page: 1,
    page_size: 25,
  });
  const [selected, setSelected] = useState<CompanySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void refreshStats();
    void runSearch(buildPayload(initialFormState), { initial: true });
  }, []);

  async function refreshStats(): Promise<void> {
    try {
      const response = await fetch("/api/v1/competitive-intelligence/statistics");
      if (!response.ok) {
        throw new Error(`Failed to load statistics (HTTP ${response.status})`);
      }

      const payload = (await response.json()) as CompanyStatistics;
      setStats(payload);
    } catch (fetchError) {
      console.error(fetchError);
      setStats(emptyStats);
    }
  }

  async function runSearch(
    payload: CompanySearchRequest,
    options?: { initial?: boolean },
  ): Promise<void> {
    if (options?.initial) {
      setLoading(true);
    } else {
      setSearching(true);
    }

    setError(null);

    try {
      const response = await fetch("/api/v1/competitive-intelligence/companies/search", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch companies (HTTP ${response.status})`);
      }

      const body = (await response.json()) as CompanySearchResponse;
      setResults(body);
      if (body.companies.length === 0) {
        setSelected(null);
      } else if (!selected) {
        setSelected(body.companies[0]);
      } else {
        const match = body.companies.find((company) => company.id === selected.id);
        setSelected(match ?? body.companies[0]);
      }
    } catch (fetchError) {
      console.error(fetchError);
      setResults({
        companies: [],
        total: 0,
        page: 1,
        page_size: payload.page_size,
      });
      setSelected(null);
      setError(
        fetchError instanceof Error
          ? fetchError.message
          : "The search request could not be completed.",
      );
    } finally {
      setLoading(false);
      setSearching(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void runSearch(buildPayload(form));
  }

  function handleReset(): void {
    setForm(initialFormState);
    void runSearch(buildPayload(initialFormState));
  }

  return (
    <div className="competitive-intelligence">
      <section className="competitive-intelligence__hero">
        <span className="competitive-intelligence__eyebrow">Competitive Intelligence</span>
        <h2>{app.name}</h2>
        <p>
          {app.goal ||
            "Track companies, markets, and signals from one stable desktop surface."}
        </p>
      </section>

      <section className="competitive-intelligence__stats">
        <article className="competitive-intelligence__stat-card">
          <div className="competitive-intelligence__stat-label">Companies</div>
          <div className="competitive-intelligence__stat-value">
            {stats.total_companies}
          </div>
        </article>
        <article className="competitive-intelligence__stat-card">
          <div className="competitive-intelligence__stat-label">Verified</div>
          <div className="competitive-intelligence__stat-value">
            {stats.verified_companies}
          </div>
        </article>
        <article className="competitive-intelligence__stat-card">
          <div className="competitive-intelligence__stat-label">Industries</div>
          <div className="competitive-intelligence__stat-value">
            {stats.industries_count}
          </div>
        </article>
        <article className="competitive-intelligence__stat-card">
          <div className="competitive-intelligence__stat-label">Countries</div>
          <div className="competitive-intelligence__stat-value">
            {stats.countries_count}
          </div>
        </article>
      </section>

      <section className="competitive-intelligence__panel">
        <div className="competitive-intelligence__panel-header">
          <h3>Search Workspace</h3>
          <span>Uses the canonical `/api/v1/competitive-intelligence/*` contract.</span>
        </div>

        <form onSubmit={handleSubmit} className="competitive-intelligence__search-grid">
          <label className="competitive-intelligence__field">
            <span>Search</span>
            <input
              type="text"
              placeholder="Company name, description, or domain"
              value={form.query}
              onChange={(event) =>
                setForm((current) => ({ ...current, query: event.target.value }))
              }
            />
          </label>

          <label className="competitive-intelligence__field">
            <span>Industry</span>
            <input
              type="text"
              placeholder="Apparel, retail, logistics"
              value={form.industry}
              onChange={(event) =>
                setForm((current) => ({ ...current, industry: event.target.value }))
              }
            />
          </label>

          <label className="competitive-intelligence__field">
            <span>Country</span>
            <input
              type="text"
              placeholder="United States, Brazil, Japan"
              value={form.country}
              onChange={(event) =>
                setForm((current) => ({ ...current, country: event.target.value }))
              }
            />
          </label>

          <div className="competitive-intelligence__actions">
            <button
              className="competitive-intelligence__button competitive-intelligence__button--primary"
              type="submit"
              disabled={loading || searching}
            >
              {searching ? "Searching..." : "Search"}
            </button>
            <button
              className="competitive-intelligence__button competitive-intelligence__button--secondary"
              type="button"
              onClick={handleReset}
              disabled={loading || searching}
            >
              Reset
            </button>
          </div>
        </form>
      </section>

      {error && <div className="competitive-intelligence__error">{error}</div>}

      <section className="competitive-intelligence__detail">
        <div className="competitive-intelligence__panel">
          <div className="competitive-intelligence__panel-header">
            <h3>Results</h3>
            <span>
              {loading
                ? "Loading current dataset..."
                : `${results.total} company records in the current slice`}
            </span>
          </div>

          <div className="competitive-intelligence__results">
            {loading ? (
              <div className="competitive-intelligence__empty">
                <strong>Loading workspace...</strong>
                The desktop is syncing the latest competitive intelligence snapshot.
              </div>
            ) : results.companies.length === 0 ? (
              <div className="competitive-intelligence__empty">
                <strong>No companies available yet.</strong>
                The backend contract is healthy, but the dataset is still empty. This is a
                safe state for the self-evolution loop: the app stays mountable while future
                evolutions add ingestion, timeline, and export capabilities.
              </div>
            ) : (
              results.companies.map((company) => (
                <article
                  key={company.id}
                  className="competitive-intelligence__card"
                  onClick={() => setSelected(company)}
                >
                  <div className="competitive-intelligence__card-header">
                    <div>
                      <h4>{company.name}</h4>
                      {company.website && <p>{company.website}</p>}
                    </div>
                    {company.is_verified && (
                      <span className="competitive-intelligence__verified">Verified</span>
                    )}
                  </div>

                  {company.description && <p>{company.description}</p>}

                  <div className="competitive-intelligence__meta">
                    <span>{company.industry || "Industry unknown"}</span>
                    <span>{company.country || "Country unknown"}</span>
                    <span>{formatMaybeNumber(company.employee_count)} employees</span>
                    <span>Confidence {formatConfidence(company.confidence_score)}</span>
                  </div>
                </article>
              ))
            )}
          </div>
        </div>

        <aside className="competitive-intelligence__detail-stack">
          <div className="competitive-intelligence__detail-card">
            <h3>Company Detail</h3>
            {selected ? (
              <>
                <p>
                  The canonical shell can already inspect the summary contract for
                  <strong> {selected.name}</strong>. Rich detail, timeline, and market signals
                  will land in later evolutions without destabilizing this root.
                </p>
                <div className="competitive-intelligence__detail-grid">
                  <div>
                    <strong>Domain</strong>
                    <span>{selected.domain || selected.website || "Not available"}</span>
                  </div>
                  <div>
                    <strong>Founded</strong>
                    <span>{selected.founded_year || "Unknown"}</span>
                  </div>
                  <div>
                    <strong>Confidence</strong>
                    <span>{formatConfidence(selected.confidence_score)}</span>
                  </div>
                  <div>
                    <strong>Last Updated</strong>
                    <span>{formatUpdated(selected.last_updated)}</span>
                  </div>
                </div>
              </>
            ) : (
              <p>
                Select a result to inspect its stable summary fields. When there is no data,
                the app remains usable and the desktop stays intact.
              </p>
            )}
            <div className="competitive-intelligence__hint">
              This implementation deliberately favors a stable, empty-safe contract over
              speculative generated UI fragments.
            </div>
          </div>

          <div className="competitive-intelligence__detail-side">
            <h3>Operational Notes</h3>
            <p>
              This root is the canonical desktop entry for the app. Legacy
              `CompanyDiscovery` variants should not live under `frontend/src/apps/`.
            </p>
          </div>
        </aside>
      </section>
    </div>
  );
}
