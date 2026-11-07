import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  Activity, ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpRight, BadgeCheck, Banknote,
  Check, CheckCircle2, ChevronRight, CircleAlert, Clock3, FileCheck2,
  FileJson2, FileSearch2, Fingerprint, Gauge, Hash, Layers3, LockKeyhole,
  Menu, PlayCircle, Search, ShieldCheck, Sparkles, TriangleAlert, UsersRound, X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { dateLabel, money, shortHash, titleCase } from "@/lib/utils";

type AnyRecord = Record<string, any>;
type Summary = { engagement: AnyRecord; packets: number; change_orders: number };
type Detail = {
  engagement: AnyRecord; packet: AnyRecord | null; approvals: AnyRecord[]; gate: AnyRecord;
  receipt: AnyRecord | null; handoff: string | null; evidence_count?: number; packet_count?: number;
};
type Receipt = { body: AnyRecord; signature: string; public_key?: string; algorithm?: string; body_hash?: string; acceptance?: AnyRecord | null };
type CheckResult = { ok: boolean; summary: string; checks: AnyRecord[] };

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...options?.headers }, ...options });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.detail || body?.message || body?.error || `Request failed (${response.status})`);
  return body as T;
}

function useResource<T>(loader: () => Promise<T>, dependencies: unknown[] = []) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    loader().then(setValue).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load data."))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);
  useEffect(() => { reload(); }, [reload]);
  return { value, error, loading, reload };
}

function Logo() {
  return <Link to="/" className="brand" aria-label="Scope Guard dashboard">
    <span className="brand-symbol"><span /></span>
    <span className="brand-copy">scope<span>guard</span><small>COMMITMENT CONTROL</small></span>
  </Link>;
}

function useLedgerNavigation() {
  const navigate = useNavigate();
  return (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    navigate("/#ledger");
    let attempts = 0;
    const scrollWhenReady = () => {
      const ledger = document.getElementById("ledger");
      if (ledger) ledger.scrollIntoView({ behavior: "smooth", block: "start" });
      else if (attempts++ < 120) window.requestAnimationFrame(scrollWhenReady);
    };
    window.requestAnimationFrame(scrollWhenReady);
  };
}

function App() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const openLedger = useLedgerNavigation();
  const { value: health } = useResource(() => request<AnyRecord>("/api/health"), []);
  const pageTitle = useMemo(() => {
    if (location.pathname === "/") return "Overview";
    if (location.pathname === "/demo") return "Product demo";
    if (location.pathname.endsWith("/change-order")) return "Signed change order";
    if (location.pathname.startsWith("/engagements/")) return "Engagement";
    if (location.pathname.startsWith("/change-orders/")) return "Change order";
    if (location.pathname === "/verify") return "Verify receipt";
    if (location.pathname === "/status") return "System status";
    return "Workspace";
  }, [location.pathname]);
  useEffect(() => setMobileOpen(false), [location.pathname, location.hash]);
  useEffect(() => {
    if (location.pathname !== "/") return;
    if (location.hash !== "#ledger") {
      if (!location.hash) window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }

    // The dashboard is data-backed, so the target may mount after the route changes.
    let attempts = 0;
    let frame = 0;
    const findLedger = () => {
      const ledger = document.getElementById("ledger");
      if (ledger) {
        ledger.scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (attempts++ < 60) {
        frame = window.requestAnimationFrame(findLedger);
      }
    };
    frame = window.requestAnimationFrame(findLedger);
    return () => window.cancelAnimationFrame(frame);
  }, [location.pathname, location.hash]);

  const openOverview = (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    setMobileOpen(false);
    if (location.pathname === "/" && !location.hash) window.scrollTo({ top: 0, behavior: "smooth" });
    else navigate("/");
  };

  return <><a href="#main" className="skip-link">Skip to main content</a><div className="app-shell">
    {mobileOpen && <button aria-label="Close navigation" className="scrim" onClick={() => setMobileOpen(false)} />}
    <aside className={`sidebar ${mobileOpen ? "sidebar-open" : ""}`}>
      <div className="sidebar-top"><Logo /><button className="icon-button mobile-close" aria-label="Close navigation" onClick={() => setMobileOpen(false)}><X size={18} /></button></div>
      <div className="workspace-switcher"><span className="workspace-icon">N</span><span><strong>Northwind workspace</strong><small>Change-order ledger</small></span></div>
      <nav className="main-nav" aria-label="Main navigation">
        <p className="nav-caption">WORKSPACE</p>
        <a href="/" onClick={openOverview} aria-current={location.pathname === "/" && !location.hash ? "page" : undefined} className={`nav-link ${location.pathname === "/" && !location.hash ? "active" : ""}`}><Layers3 size={17} />Overview</a>
        <a href="/#ledger" onClick={(event) => { setMobileOpen(false); openLedger(event); }} aria-current={location.pathname === "/" && location.hash === "#ledger" ? "location" : undefined} className={`nav-link ${location.pathname === "/" && location.hash === "#ledger" ? "active" : ""}`}><FileCheck2 size={17} />Change orders</a>
        <NavLink to="/demo" className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}><PlayCircle size={17} />Watch demo</NavLink>
        <NavLink to="/verify" className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}><FileSearch2 size={17} />Verify receipt</NavLink>
        <p className="nav-caption nav-caption-system">SYSTEM</p>
        <NavLink to="/status" className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}><Activity size={17} />API status</NavLink>
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-integrity"><span className={`pulse-dot ${health?.audit_ok ? "" : "pulse-dot-alert"}`} /><span><strong>Ledger integrity</strong><small>{health?.audit_ok ? "All systems verified" : "Review required"}</small></span>{health?.audit_ok ? <Check size={15} /> : <TriangleAlert size={15} />}</div>
        <Separator className="sidebar-separator" />
        <div className="profile-row"><span className="profile-avatar">SG</span><span><strong>Scope Guard</strong><small>Workspace owner</small></span></div>
      </div>
    </aside>

    <div className="app-main">
      <header className="topbar">
        <button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileOpen(true)}><Menu size={19} /></button>
        <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={14} /><strong>{pageTitle}</strong></div>
        <div className="topbar-right"><span className="secure-status"><LockKeyhole size={14} /><span>SECURE LEDGER</span></span><span className="top-avatar">SG</span></div>
      </header>
      <main id="main" className="page-container"><Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/demo" element={<DemoPage />} />
        <Route path="/engagements/:engagementId" element={<EngagementPage />} />
        <Route path="/engagements/:engagementId/change-order" element={<LatestReceiptPage />} />
        <Route path="/change-orders/:receiptId" element={<ReceiptPage />} />
        <Route path="/verify" element={<VerifyPage />} />
        <Route path="/status" element={<StatusPage />} />
        <Route path="*" element={<NotFound />} />
      </Routes></main>
      <footer className="site-footer"><span><strong>scopeguard</strong> Every promise has a price.</span><span>Deterministic pricing <i>·</i> Human signatures <i>·</i> Built for Switch</span></footer>
    </div>
  </div></>;
}

function SectionHeading({ eyebrow, title, detail, action }: { eyebrow?: string; title: string; detail?: string; action?: React.ReactNode }) {
  return <div className="section-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2>{title}</h2>{detail && <p className="section-detail">{detail}</p>}</div>{action}</div>;
}

function Loading({ label = "Loading your workspace" }: { label?: string }) {
  return <div className="loading-state"><span className="loader" /><span>{label}</span></div>;
}

function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="error-state"><CircleAlert size={18} /><div><strong>We couldn’t load this view</strong><p>{message}</p>{retry && <Button size="sm" variant="outline" onClick={retry}>Try again</Button>}</div></div>;
}

function Dashboard() {
  const [query, setQuery] = useState("");
  const openLedger = useLedgerNavigation();
  const { value, error, loading, reload } = useResource(async () => {
    const [health, rows, audit] = await Promise.all([
      request<AnyRecord>("/api/health"), request<Summary[]>("/api/engagements"), request<AnyRecord>("/api/audit"),
    ]);
    const cases = await Promise.all(rows.map(async (row) => ({ ...row, ...(await request<Detail>(`/api/engagements/${encodeURIComponent(row.engagement.id)}`)) })));
    const receipts = await request<Receipt[]>("/api/change-orders");
    return { health, cases, audit, receipts };
  });
  const cases = value?.cases ?? [];
  const filteredCases = cases.filter((row) => `${row.engagement.client} ${row.engagement.id} ${row.packet?.status ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  const stats = value?.health?.stats ?? {};
  const totalCommitment = cases.reduce((sum, row) => sum + Number(row.packet?.total ?? row.receipt?.body?.total ?? 0), 0);
  const pending = cases.reduce((sum, row) => sum + (row.gate?.pending?.length ?? 0), 0);

  if (loading) return <Loading />;
  if (error) return <ErrorState message={error} retry={reload} />;

  return <div className="dashboard-page">
    <section className="hero-panel">
      <div className="hero-grid-art" aria-hidden="true" />
      <div className="hero-content">
        <Badge variant="default" className="hero-badge"><Sparkles size={12} />SCOPE OPERATIONS</Badge>
        <h1>Every request.<br />A <em>clear commitment.</em></h1>
        <p>Price the change. Challenge the risk. Get the right people to sign before delivery begins.</p>
        <div className="hero-actions"><Button asChild size="lg"><Link to="/#ledger" onClick={openLedger}>Open live ledger <ArrowUpRight size={16} /></Link></Button><Link to="/demo" className="hero-text-link"><PlayCircle size={16} />Watch demo</Link><Link to="/verify" className="hero-text-link">Verify a receipt <ArrowRight size={15} /></Link></div>
      </div>
      <div className="hero-preview" aria-label="Current Northwind commitment summary">
        <div className="preview-top"><span className="preview-icon"><FileCheck2 size={15} /></span><span className="preview-label">CURRENT COMMITMENT</span><Badge variant={value?.health?.audit_ok ? "success" : "destructive"}>{value?.health?.audit_ok ? "VERIFIED" : "REVIEW"}</Badge></div>
        <div className="preview-client">Northwind Health<span>northwind-2026</span></div>
        <div className="preview-value">{money(cases[0]?.packet?.total ?? cases[0]?.receipt?.body?.total ?? 0, cases[0]?.engagement?.currency || "USD")}</div>
        <div className="preview-caption">priced scope · revision {cases[0]?.packet?.version ?? "—"}</div>
        <div className="preview-chart"><svg viewBox="0 0 300 58" role="img" aria-label="Commitment trend preview"><path d="M1 48 C26 42 30 46 51 35 S82 43 100 26 S128 34 149 24 S178 29 196 16 S222 28 244 14 S275 22 299 5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"/><path d="M1 57 C26 51 30 55 51 44 S82 52 100 35 S128 43 149 33 S178 38 196 25 S222 37 244 23 S275 31 299 14 V58 H1Z" fill="url(#chartFill)"/><defs><linearGradient id="chartFill" x1="0" x2="0" y1="0" y2="1"><stop stopColor="#c7f34b" stopOpacity=".22"/><stop offset="1" stopColor="#c7f34b" stopOpacity="0"/></linearGradient></defs></svg><span>Scope is measured, never assumed.</span></div>
        <div className="preview-foot"><span><span className="tiny-dot" />Evidence reviewed</span><span><UsersRound size={13} />4 human roles</span></div>
      </div>
      <div className="hero-index">01 <span>/</span>04</div>
    </section>

    <section className="metric-grid" aria-label="Ledger summary">
      <MetricCard label="Tracked engagements" value={stats.engagements ?? cases.length} icon={<Layers3 />} note="Across active client work" />
      <MetricCard label="Priced packets" value={stats.packets ?? cases.filter((row) => row.packet).length} icon={<Banknote />} note="Deterministic policy output" />
      <MetricCard label="Awaiting signatures" value={pending} icon={<UsersRound />} note="Human approval required" accent={pending > 0} />
      <MetricCard label="Total at stake" value={money(totalCommitment, cases[0]?.engagement?.currency || "USD")} icon={<Gauge />} note="Current priced commitments" />
      <Card className={`integrity-card ${value?.health?.audit_ok ? "integrity-good" : "integrity-bad"}`}>
        <CardContent className="integrity-content"><span className="integrity-icon">{value?.health?.audit_ok ? <ShieldCheck /> : <TriangleAlert />}</span><span><small>AUDIT CHAIN</small><strong>{value?.health?.audit_ok ? "Intact" : "Needs review"}</strong><em>{stats.audit ?? value?.audit?.detail?.events ?? 0} chained events</em></span><span className="integrity-arrow">{value?.health?.audit_ok ? <CheckCircle2 /> : <CircleAlert />}</span></CardContent>
      </Card>
    </section>

    <section id="workflow" className="workflow-section">
      <SectionHeading eyebrow="HOW SCOPE GUARD WORKS" title="A tighter loop from ask to signed scope" detail="Each step produces an artifact the next person can trust." />
      <div className="workflow-grid">
        <WorkflowStep number="01" icon={<FileSearch2 />} name="Ground the request" detail="Scout turns the conversation into typed asks and dated source evidence." tag="EVIDENCE" />
        <WorkflowStep number="02" icon={<Banknote />} name="Price the change" detail="Policy prices the same inputs the same way, every time." tag="DETERMINISTIC" />
        <WorkflowStep number="03" icon={<TriangleAlert />} name="Challenge the answer" detail="Red Team checks margin, autonomy, and stale evidence before approval." tag="INDEPENDENT REVIEW" />
        <WorkflowStep number="04" icon={<Fingerprint />} name="Sign what ships" detail="Exact human signatures lock to the packet hash before handoff." tag="HASH-BOUND" />
      </div>
    </section>

    <section id="ledger" className="ledger-section">
      <SectionHeading eyebrow="LIVE CHANGE-ORDER LEDGER" title="Decisions with a paper trail" detail="The visible record of what was requested, reviewed, and approved."
        action={<div className="search-wrap"><Search size={15} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search ledger" aria-label="Search ledger" /></div>} />
      <Card className="ledger-card"><Table>
        <TableHeader><TableRow><TableHead>Engagement</TableHead><TableHead>Requests</TableHead><TableHead>Packet</TableHead><TableHead>Commitment</TableHead><TableHead>Status</TableHead><TableHead className="table-action-head"> </TableHead></TableRow></TableHeader>
        <TableBody>{filteredCases.map((row) => <TableRow key={row.engagement.id}>
          <TableCell><Link to={`/engagements/${encodeURIComponent(row.engagement.id)}`} className="engagement-link"><span className="client-avatar">{String(row.engagement.client ?? "N").slice(0, 1).toUpperCase()}</span><span><strong>{row.engagement.client}</strong><small>{row.engagement.id}</small></span></Link></TableCell>
          <TableCell><span className="cell-primary">{row.engagement.requests?.length ?? 0} requests</span><small className="cell-secondary">{row.evidence_count ?? 0} evidence items</small></TableCell>
          <TableCell><span className="mono">{row.packet ? `v${row.packet.version}` : "—"}</span><small className="cell-secondary">{row.packet ? shortHash(row.packet.packet_hash, 10) : "Not priced"}</small></TableCell>
          <TableCell className="money-cell">{money(row.packet?.total ?? row.receipt?.body?.total ?? 0, row.engagement.currency || "USD")}</TableCell>
          <TableCell><StatusBadge value={row.receipt ? (row.receipt.acceptance ? "countersigned" : "sealed") : row.packet?.status ?? "ingested"} /></TableCell>
          <TableCell className="table-action"><Link to={`/engagements/${encodeURIComponent(row.engagement.id)}`} aria-label={`Open ${row.engagement.client}`}><ArrowUpRight size={17} /></Link></TableCell>
        </TableRow>)}</TableBody>
      </Table>{filteredCases.length === 0 && <div className="table-empty"><Search size={18} /><span>No matching engagements</span><small>Try another client name or engagement ID.</small></div>}</Card>
    </section>

    {(value?.receipts?.length ?? 0) > 0 && <section className="recent-section"><SectionHeading eyebrow="SIGNED RECORDS" title="Recent change orders" detail="Signed artifacts can be independently verified at any time." action={<Button variant="outline" asChild><Link to={`/change-orders/${value!.receipts[0].body.receipt_id}`}>View latest <ArrowRight /></Link></Button>} />
      <div className="receipt-grid">{value!.receipts.slice(0, 3).map((receipt: Receipt) => <ReceiptCard key={receipt.body.receipt_id} receipt={receipt} />)}</div>
    </section>}
  </div>;
}

function DemoPage() {
  const asset = (name: string) => `${import.meta.env.BASE_URL}${name}`;
  return <div className="demo-page">
    <header className="demo-intro">
      <Badge variant="outline"><PlayCircle size={13} /> PRODUCT WALKTHROUGH</Badge>
      <h1>Scope changes, made clear.</h1>
      <p>See how Scope Guard prices requests, challenges risk, and verifies the signed handoff. This walkthrough includes a deterministic product replay and a read-only Codex conversation in Switch.</p>
    </header>
    <section className="demo-player-card" aria-label="Scope Guard demo video">
      <video className="demo-player" controls playsInline preload="metadata" poster={asset("demo-poster.png")} aria-label="Scope Guard product walkthrough">
        <source src={asset("scopeguard-demo-youtube.mp4")} type="video/mp4" />
        <track kind="captions" src={asset("narration.vtt")} srcLang="en" label="English" default />
        Your browser does not support HTML video. <a href={asset("scopeguard-demo-youtube.mp4")}>Download the demo video</a>.
      </video>
      <div className="demo-player-footer"><span><strong>Scope Guard</strong><small>Priced. Approved. Proven.</small></span><a href={asset("scopeguard-demo-youtube.mp4")} download>Download video <ArrowDownToLine size={15} /></a></div>
    </section>
    <p className="demo-note"><CircleAlert size={15} /> The Switch conversation shown is a solo project-owner request to Codex. It does not depict teammate approvals.</p>
  </div>;
}

function MetricCard({ label, value, icon, note, accent }: { label: string; value: React.ReactNode; icon: React.ReactNode; note: string; accent?: boolean }) {
  return <Card className="metric-card"><CardContent className="metric-content"><div className="metric-top"><span>{label}</span><span className={`metric-icon ${accent ? "metric-icon-accent" : ""}`}>{icon}</span></div><strong className="metric-value">{value}</strong><small>{note}</small></CardContent></Card>;
}

function WorkflowStep({ number, icon, name, detail, tag }: { number: string; icon: React.ReactNode; name: string; detail: string; tag: string }) {
  return <Card className="workflow-card"><CardContent><div className="workflow-card-top"><span className="workflow-icon">{icon}</span><span className="step-number">{number}</span></div><h3>{name}</h3><p>{detail}</p><span className="workflow-tag">{tag}</span></CardContent></Card>;
}

function StatusBadge({ value }: { value?: string }) {
  const status = String(value || "unknown").toLowerCase();
  const variant = status.includes("counter") || status.includes("approve") || status === "valid" || status === "ok" ? "success" : status.includes("block") || status.includes("refus") || status.includes("fail") ? "destructive" : status.includes("pending") || status.includes("await") || status.includes("narrow") || status.includes("warn") ? "warning" : "secondary";
  return <Badge variant={variant as any}><span className="badge-dot" />{titleCase(status)}</Badge>;
}

function ReceiptCard({ receipt }: { receipt: Receipt }) {
  return <Link to={`/change-orders/${receipt.body.receipt_id}`} className="receipt-link-card"><Card className="receipt-card-small"><CardContent><div className="receipt-small-top"><FileCheck2 size={18} /><StatusBadge value={receipt.acceptance ? "countersigned" : "sealed"} /></div><strong>{receipt.body.client}</strong><span className="mono">{receipt.body.receipt_id}</span><div className="receipt-small-foot"><span>{money(receipt.body.total, receipt.body.currency)}</span><ArrowUpRight size={15} /></div></CardContent></Card></Link>;
}

function EngagementPage() {
  const { engagementId = "" } = useParams();
  const { value, error, loading, reload } = useResource(() => request<Detail>(`/api/engagements/${encodeURIComponent(engagementId)}`), [engagementId]);
  const { value: audit } = useResource(() => request<AnyRecord>("/api/audit"), []);
  if (loading) return <Loading label="Loading engagement details" />;
  if (error || !value) return <ErrorState message={error || "Engagement not found."} retry={reload} />;
  const { engagement, packet, approvals, gate, receipt, handoff } = value;
  return <div className="detail-page">
    <div className="crumb-row"><Link to="/">Overview</Link><ChevronRight size={14} /><span>{engagement.id}</span></div>
    <header className="detail-header"><div><p className="eyebrow">CHANGE-ORDER ENGAGEMENT</p><h1>{engagement.client}</h1><p className="detail-subtitle">{engagement.id} <span>·</span> requested by {engagement.requested_by} <span>·</span> {engagement.currency} {money(engagement.contract_value, engagement.currency)} fixed fee</p></div><div className="detail-state"><StatusBadge value={receipt ? (receipt.acceptance ? "countersigned" : "sealed") : packet?.status ?? "ingested"} />{receipt && <Link to={`/change-orders/${receipt.body.receipt_id}`} className="mono">{receipt.body.receipt_id}<ArrowUpRight size={14} /></Link>}</div></header>
    <div className="detail-layout"><div className="detail-main">
      {packet ? <Card className="data-card"><CardHeader className="data-card-header"><div><Badge variant="outline"><Layers3 size={12} /> PACKET V{packet.version}</Badge><CardTitle>Priced commitments</CardTitle><CardDescription>Each decision is priced against the active policy and current packet hash.</CardDescription></div><span className="hash-pill"><Hash size={13} />{shortHash(packet.packet_hash, 18)}</span></CardHeader><CardContent className="decision-list">
        {(packet.decisions ?? []).map((decision: AnyRecord) => <Decision key={decision.request_id} decision={decision} currency={engagement.currency} approvals={approvals.filter((item) => item.request_id === decision.request_id)} />)}
        <div className="packet-totals"><span>Subtotal<strong>{money(packet.subtotal, packet.currency)}</strong></span><span>Discount<strong>{money(packet.discount_total, packet.currency)}</strong></span><span className="packet-total">Total<strong>{money(packet.total, packet.currency)}</strong></span></div>
      </CardContent></Card> : <Card><CardContent className="empty-panel"><Banknote size={24} /><h3>No priced packet yet</h3><p>Evidence is present. Run the pricing assessment to create the first packet.</p><code>scopeguard assess --engagement {engagement.id}</code></CardContent></Card>}

      {!!packet?.findings?.length && <Card className="data-card"><CardHeader className="data-card-header"><div><CardTitle>Red Team findings</CardTitle><CardDescription>Independent review findings attached to this packet.</CardDescription></div><Badge variant={packet.findings.some((finding: AnyRecord) => finding.severity === "block") ? "destructive" : "warning"}>{packet.findings.filter((finding: AnyRecord) => finding.severity === "block").length} blocking</Badge></CardHeader><CardContent className="finding-list">{packet.findings.map((finding: AnyRecord, index: number) => <Finding key={`${finding.code}-${index}`} finding={finding} />)}</CardContent></Card>}

      {!!approvals.length && <Card className="data-card"><CardHeader className="data-card-header"><div><CardTitle>Signature record</CardTitle><CardDescription>Human approvals are exact phrases bound to a packet version.</CardDescription></div><div className="approval-summary"><StatusBadge value="valid" />{approvals.filter((item) => item.status === "valid").length} valid <span>·</span> {approvals.filter((item) => item.status !== "valid").length} stale</div></CardHeader><CardContent className="table-card-content"><Table><TableHeader><TableRow><TableHead>Request</TableHead><TableHead>Role</TableHead><TableHead>Signer</TableHead><TableHead>Phrase</TableHead><TableHead>Status</TableHead><TableHead>Bound hash</TableHead></TableRow></TableHeader><TableBody>{approvals.map((item, index) => <TableRow key={`${item.actor}-${index}`}><TableCell className="mono">{item.request_id}</TableCell><TableCell>{titleCase(item.role)}</TableCell><TableCell>{item.actor}</TableCell><TableCell className="mono phrase-cell">{item.phrase}</TableCell><TableCell><StatusBadge value={item.status} /></TableCell><TableCell className="mono muted-cell">{shortHash(item.packet_hash, 10)}</TableCell></TableRow>)}</TableBody></Table></CardContent></Card>}

      {handoff && <Card className="data-card"><CardHeader><CardTitle>Delivery handoff</CardTitle><CardDescription>The exact signed scope passed to the linked delivery room.</CardDescription></CardHeader><CardContent><pre className="handoff-block">{handoff}</pre></CardContent></Card>}
    </div>
    <aside className="detail-rail">
      <Card className="gate-card"><CardHeader><CardTitle><ShieldCheck size={17} /> Human approval gate</CardTitle><CardDescription>Seal only after every check passes.</CardDescription></CardHeader><CardContent>{gate ? <><div className={`gate-result ${gate.ok ? "gate-clear" : "gate-pending"}`}><span>{gate.ok ? <CheckCircle2 size={18} /> : <Clock3 size={18} />}</span><strong>{gate.ok ? "Clear to seal" : "Approval required"}</strong></div><div className="gate-checks">{(gate.checks ?? []).map((check: AnyRecord) => <div className="gate-check" key={check.code}><span className={`gate-check-icon ${check.ok ? "passed" : "failed"}`}>{check.ok ? <Check size={12} /> : <X size={12} />}</span><span><strong>{titleCase(check.code)}</strong><small>{check.detail}</small></span></div>)}</div>{(gate.pending?.length ?? 0) > 0 && <div className="pending-box"><span>Pending signatures</span><div>{gate.pending.map((item: string) => <Badge key={item} variant="warning">{item}</Badge>)}</div></div>}</> : <p className="muted-copy">No packet to gate yet.</p>}</CardContent></Card>
      {receipt && <Card><CardHeader><CardTitle>Signed change order</CardTitle><CardDescription>Cryptographically verifiable receipt.</CardDescription></CardHeader><CardContent><Link to={`/change-orders/${receipt.body.receipt_id}`} className="receipt-open"><span><FileCheck2 size={17} />{receipt.body.receipt_id}</span><ArrowUpRight size={15} /></Link><div className="rail-meta"><span>Total</span><strong>{money(receipt.body.total, receipt.body.currency)}</strong><span>Issued</span><strong>{dateLabel(receipt.body.issued_at)}</strong><span>Client</span><strong>{receipt.acceptance ? "Countersigned" : "Awaiting acceptance"}</strong></div><small className="hash-note">Body hash · {shortHash(receipt.body_hash, 16)}</small></CardContent></Card>}
      <Card><CardHeader><CardTitle>Engagement facts</CardTitle></CardHeader><CardContent><div className="fact-list"><span>Requests</span><strong>{engagement.requests?.length ?? 0}</strong><span>Evidence</span><strong>{value.evidence_count ?? "—"}</strong><span>Packets</span><strong>{value.packet_count ?? 1}</strong><span>Delivery cost ratio</span><strong>{engagement.delivery_cost_ratio_pct}%</strong><span>Contract value</span><strong>{money(engagement.contract_value, engagement.currency)}</strong></div><Separator className="fact-separator" /><p className="context-copy">{engagement.context}</p></CardContent></Card>
      <Card><CardHeader><CardTitle>Audit trail</CardTitle><CardDescription>Latest ledger events</CardDescription></CardHeader><CardContent><ol className="audit-timeline">{(audit?.events ?? []).slice(-6).reverse().map((event: AnyRecord, index: number) => <li key={`${event.kind}-${index}`}><span className="timeline-dot" /><span><strong>{titleCase(event.kind)}</strong><small>{event.actor} · {new Date(event.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></span></li>)}</ol><Link to="/status" className="text-link">View full audit chain <ArrowRight size={14} /></Link></CardContent></Card>
    </aside></div>
  </div>;
}

function Decision({ decision, currency, approvals }: { decision: AnyRecord; currency: string; approvals: AnyRecord[] }) {
  const type = String(decision.verdict || "").toLowerCase();
  return <article className={`decision-row decision-${type}`}><div className="decision-top"><span className="claim-id">{decision.request_id}</span><StatusBadge value={decision.verdict} /><span className="required-roles">Needs {decision.required_roles?.join(" + ") || "review"}</span>{decision.amount !== null && decision.amount !== undefined && <strong className="decision-amount">{money(decision.amount, currency)}</strong>}</div><p className="decision-scope">{decision.priced_text}</p>{decision.conditions?.length > 0 && <ul className="condition-list">{decision.conditions.map((condition: string) => <li key={condition}>{condition}</li>)}</ul>}<p className="decision-rationale">{decision.rationale}</p><div className="decision-meta"><span>Policy · {(decision.policy_refs ?? []).join(" · ") || "No policy reference"}</span><span>Evidence · {(decision.evidence_ids ?? []).join(" · ") || "No attached source"}</span></div><div className="decision-signatures">{approvals.length ? approvals.map((item, index) => <Badge key={`${item.role}-${index}`} variant="success"><Check size={11} />{titleCase(item.role)} · {item.actor}</Badge>) : <Badge variant="warning">Awaiting {decision.required_roles?.join(" + ") || "approval"}</Badge>}</div></article>;
}

function Finding({ finding }: { finding: AnyRecord }) {
  const blocked = finding.severity === "block";
  return <div className={`finding-row ${blocked ? "finding-block" : "finding-note"}`}><span className="finding-icon">{blocked ? <TriangleAlert size={16} /> : <CircleAlert size={16} />}</span><span className="finding-copy"><strong>{titleCase(finding.code)} <span>· {finding.request_id || "Packet"}</span></strong><small>{finding.message}</small></span><Badge variant={blocked ? "destructive" : "warning"}>{finding.severity}</Badge></div>;
}

function LatestReceiptPage() {
  const { engagementId = "" } = useParams();
  const { value, error, loading, reload } = useResource(async () => {
    const summary = await request<AnyRecord>(`/api/engagements/${encodeURIComponent(engagementId)}`);
    if (!summary.receipt?.body?.receipt_id) throw new Error("No signed change order exists for this engagement yet.");
    return summary.receipt.body.receipt_id as string;
  }, [engagementId]);
  if (loading) return <Loading label="Finding latest receipt" />;
  if (error || !value) return <ErrorState message={error || "No receipt found."} retry={reload} />;
  return <ReceiptDetails receiptId={value} />;
}

function ReceiptPage() {
  const { receiptId = "" } = useParams();
  return <ReceiptDetails receiptId={receiptId} />;
}

function ReceiptDetails({ receiptId }: { receiptId: string }) {
  const { value, error, loading, reload } = useResource(async () => {
    const [receipt, result] = await Promise.all([
      request<Receipt>(`/api/change-orders/${encodeURIComponent(receiptId)}`),
      request<CheckResult>(`/api/change-orders/${encodeURIComponent(receiptId)}/verify`),
    ]);
    return { receipt, result };
  }, [receiptId]);
  if (loading) return <Loading label="Verifying signed change order" />;
  if (error || !value) return <ErrorState message={error || "Change order not found."} retry={reload} />;
  const { receipt, result } = value;
  const body = receipt.body;
  return <div className="receipt-page"><div className="crumb-row"><Link to="/">Overview</Link><ChevronRight size={14} /><Link to={`/engagements/${encodeURIComponent(body.engagement_id)}`}>{body.engagement_id}</Link><ChevronRight size={14} /><span>Signed change order</span></div>
    <div className="receipt-heading"><div><p className="eyebrow">SIGNED SCOPE ARTIFACT</p><h1>{body.client}</h1><p className="detail-subtitle">{body.receipt_id} <span>·</span> Issued {dateLabel(body.issued_at)}</p></div><div className="receipt-heading-actions"><StatusBadge value={result.ok ? (receipt.acceptance ? "countersigned" : "sealed") : "invalid"} /><Button variant="outline" asChild><a href={`/api/change-orders/${encodeURIComponent(body.receipt_id)}`} target="_blank" rel="noreferrer"><FileJson2 />Signed JSON</a></Button></div></div>
    <div className="receipt-layout"><Card className="signed-document"><div className="signed-document-banner"><div className="doc-brand"><span className="doc-mark"><ShieldCheck size={20} /></span><span><strong>Scope Guard</strong><small>CHANGE ORDER · {body.receipt_id}</small></span></div><div className={`doc-seal ${result.ok ? "" : "doc-seal-bad"}`}><BadgeCheck size={24} /><span>{receipt.acceptance && result.ok ? "TWO-PARTY" : result.ok ? "ISSUER SIGNED" : "INVALID"}</span><small>ED25519 VERIFIED</small></div></div>
      <div className="document-meta">{[["Client", body.client], ["Engagement", body.engagement_id], ["Packet", `v${body.packet_version}`], ["Policy", body.policy_id], ["Issued", dateLabel(body.issued_at)], ["Expires", dateLabel(body.expires_at)]].map(([label, value]) => <div key={label}><small>{label}</small><strong>{value}</strong></div>)}</div>
      <section className="document-section"><div className="document-section-heading"><div><h2>Signed line items</h2><p>Only these statements may be represented to the client or handed to delivery.</p></div><Badge variant="success"><CheckCircle2 size={12} />{body.line_items?.length ?? 0} commitments</Badge></div>{(body.line_items ?? []).map((item: AnyRecord) => <div className="signed-line" key={item.request_id}><div className="signed-line-top"><span className="claim-id">{item.request_id}</span><StatusBadge value={item.verdict} /><strong>{money(item.amount, body.currency)}</strong></div><p>{item.scope}</p>{item.conditions?.length > 0 && <ul className="condition-list">{item.conditions.map((condition: string) => <li key={condition}>{condition}</li>)}</ul>}</div>)}<div className="packet-totals document-totals"><span>Subtotal<strong>{money(body.subtotal, body.currency)}</strong></span><span>Discount<strong>{money(body.discount_total, body.currency)}</strong></span><span className="packet-total">Total<strong>{money(body.total, body.currency)}</strong></span></div></section>
      {!!body.declined?.length && <section className="document-section"><h2>Not committed</h2><p className="muted-copy">These requests were reviewed and receive no line item.</p><div className="chip-row">{body.declined.map((item: string) => <Badge variant="destructive" key={item}>{item}</Badge>)}</div></section>}
      <section className="document-section"><div className="document-section-heading"><div><h2>Human signatures</h2><p>Signer roles and exact phrases retained in the audit record.</p></div></div><div className="table-card-content"><Table><TableHeader><TableRow><TableHead>Request</TableHead><TableHead>Role</TableHead><TableHead>Signer</TableHead><TableHead>Exact phrase</TableHead><TableHead>Signed</TableHead></TableRow></TableHeader><TableBody>{(body.approvals ?? []).map((item: AnyRecord, index: number) => <TableRow key={`${item.actor}-${index}`}><TableCell className="mono">{item.request_id}</TableCell><TableCell>{titleCase(item.role)}</TableCell><TableCell>{item.actor}</TableCell><TableCell className="mono phrase-cell">{item.phrase}</TableCell><TableCell>{dateLabel(item.approved_at)}</TableCell></TableRow>)}</TableBody></Table></div></section>
      <section className="document-section"><h2>Client acceptance</h2>{receipt.acceptance ? <><p><strong>{receipt.acceptance.issuer}</strong> countersigned the issuer’s body hash on {dateLabel(body.issued_at)}.</p><p className="muted-copy">A separate Ed25519 signature confirms both parties agreed to this exact document.</p><details><summary>Client public key</summary><code>{receipt.acceptance.public_key}</code></details><details><summary>Client signature</summary><code>{receipt.acceptance.signature}</code></details></> : <div className="acceptance-pending"><Clock3 size={17} /><span><strong>Awaiting client countersignature</strong><small>The issuer signature proves Scope Guard produced this order, not that the client accepted it.</small></span></div>}</section>
      <section className="document-signatures"><SignatureValue label="Packet hash" value={body.packet_hash} /><SignatureValue label="Body hash" value={receipt.body_hash ?? "—"} /><SignatureValue label="Chain previous" value={body.chain_prev || "Genesis for this engagement"} /><SignatureValue label="Issuer" value={body.issuer} /></section>
    </Card><aside className="receipt-rail"><Card className={`verification-card ${result.ok ? "" : "verification-failed"}`}><CardHeader><div className="verification-card-title"><span className="verification-icon">{result.ok ? <ShieldCheck /> : <TriangleAlert />}</span><span><CardTitle>Independent verification</CardTitle><CardDescription>Recomputed from the signed artifact</CardDescription></span></div></CardHeader><CardContent><p className="verification-summary">{result.summary}</p><Checks items={result.checks} /><Button className="full-button" variant="outline" asChild><Link to="/verify">Verify another receipt <ArrowRight /></Link></Button></CardContent></Card><Card><CardHeader><CardTitle>Why this is hard to fake</CardTitle></CardHeader><CardContent><ul className="plain-list"><li>The issuer signature covers the canonical JSON body.</li><li>Each approval phrase is bound to this exact packet hash.</li><li>A price revision changes the hash and invalidates earlier approvals.</li><li>Client acceptance signs the issuer body hash independently.</li></ul></CardContent></Card><Card><CardHeader><CardTitle>Cryptographic material</CardTitle><CardDescription>Public verification values</CardDescription></CardHeader><CardContent><details><summary>Issuer public key</summary><code>{receipt.public_key}</code></details><details><summary>Issuer signature</summary><code>{receipt.signature}</code></details><p className="muted-copy">Algorithm: {receipt.algorithm} · Body hash uses SHA-256.</p></CardContent></Card></aside></div>
  </div>;
}

function SignatureValue({ label, value }: { label: string; value: string }) {
  return <div><small>{label}</small><code>{value || "—"}</code></div>;
}

function Checks({ items }: { items: AnyRecord[] }) {
  return <ul className="verification-checks">{items.map((check, index) => <li key={`${check.code}-${index}`}><span className={check.ok ? "check-passed" : "check-failed"}>{check.ok ? <Check size={12} /> : <X size={12} />}</span><span><strong>{titleCase(check.code)}</strong><small>{check.detail}</small></span></li>)}</ul>;
}

function VerifyPage() {
  const [raw, setRaw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [receiptId, setReceiptId] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null); setResult(null); setReceiptId(null);
    let parsed: unknown;
    try { parsed = JSON.parse(raw); } catch { setError("Paste a valid JSON document before verifying it."); return; }
    setWorking(true);
    try {
      const response = await fetch("/api/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(parsed) });
      const checked = await response.json() as CheckResult & { error?: string };
      if (!response.ok && typeof checked.ok !== "boolean") throw new Error(checked.error || "This document could not be verified.");
      setResult(checked);
      setReceiptId((parsed as AnyRecord)?.body?.receipt_id ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "This document could not be verified.");
    } finally { setWorking(false); }
  }
  const { value: latest } = useResource(() => request<Receipt[]>("/api/change-orders"), []);
  return <div className="verify-page"><div className="verify-intro"><p className="eyebrow">INDEPENDENT VERIFICATION</p><h1>Trust the signature.<br /><em>Check the proof.</em></h1><p>Drop in any Scope Guard change order. We recalculate every hash, signature, and total from the document itself.</p></div><div className="verify-layout"><Card className="verify-form-card"><CardHeader><div className="form-heading-icon"><FileSearch2 size={18} /></div><CardTitle>Verify a change order</CardTitle><CardDescription>Paste the complete signed JSON document to verify the issuer and client signatures.</CardDescription></CardHeader><CardContent><form onSubmit={submit} className="verification-form"><Label htmlFor="receipt-json">Change-order JSON</Label><Textarea id="receipt-json" value={raw} onChange={(event) => setRaw(event.target.value)} spellCheck={false} placeholder={'{\n  "body": { "receipt_id": "co_…" },\n  "signature": "…",\n  "acceptance": { … }\n}'} aria-describedby="verify-hint" />{error && <div className="form-error"><CircleAlert size={15} />{error}</div>}<div className="form-actions"><Button type="submit" size="lg" disabled={working || !raw.trim()}>{working ? <><span className="button-spinner" />Checking signatures…</> : <>Verify document <ArrowRight /></>}</Button><span id="verify-hint">Your document is checked in memory and never saved.</span></div></form></CardContent></Card><aside className="verify-aside">{result ? <Card className={`result-card ${result.ok ? "result-good" : "result-bad"}`}><CardHeader><div className="result-heading"><span className="result-mark">{result.ok ? <ShieldCheck /> : <TriangleAlert />}</span><span><CardTitle>{result.ok ? "Document verified" : "Verification failed"}</CardTitle><CardDescription>Cryptographic integrity check</CardDescription></span></div></CardHeader><CardContent><p className="result-summary">{result.summary}</p><Checks items={result.checks} />{receiptId && <Button variant="outline" className="full-button" asChild><Link to={`/change-orders/${receiptId}`}>Open signed change order <ArrowUpRight /></Link></Button>}</CardContent></Card> : <Card className="checks-guide"><CardHeader><div className="form-heading-icon"><LockKeyhole size={18} /></div><CardTitle>What we verify</CardTitle><CardDescription>No trust in a database flag. Every check is recalculated.</CardDescription></CardHeader><CardContent><ol className="verify-list"><li><span>01</span><div><strong>Canonical body hash</strong><small>Recomputed from signed fields</small></div></li><li><span>02</span><div><strong>Issuer signature</strong><small>Ed25519 key and signature match</small></div></li><li><span>03</span><div><strong>Approval coverage</strong><small>Required roles cover each line item</small></div></li><li><span>04</span><div><strong>Financial balance</strong><small>Subtotal, discount, and total reconcile</small></div></li><li><span>05</span><div><strong>Client acceptance</strong><small>Second signature, when included</small></div></li></ol></CardContent></Card>}{latest?.length ? <Card className="quick-receipts"><CardHeader><CardTitle>Try a recent change order</CardTitle></CardHeader><CardContent>{latest.slice(0, 3).map((receipt) => <button key={receipt.body.receipt_id} onClick={async () => { setRaw(JSON.stringify(receipt, null, 2)); setError(null); }}><span><FileJson2 size={15} />{receipt.body.receipt_id}</span><span>{money(receipt.body.total, receipt.body.currency)}</span><ArrowRight size={14} /></button>)}</CardContent></Card> : null}</aside></div></div>;
}

function StatusPage() {
  const { value, loading, error, reload } = useResource(async () => Promise.all([request<AnyRecord>("/api/health"), request<AnyRecord>("/api/audit")]));
  if (loading) return <Loading label="Checking system status" />;
  if (error || !value) return <ErrorState message={error || "Status unavailable."} retry={reload} />;
  const [health, audit] = value;
  return <div className="status-page"><div className="status-intro"><p className="eyebrow">SYSTEM HEALTH</p><h1>Everything in its place.</h1><p>Live health and integrity data from the Scope Guard service.</p></div><div className="status-grid"><Card className={health.ok ? "status-good" : "status-bad"}><CardContent><span className="status-icon"><Activity /></span><small>API SERVICE</small><strong>{health.ok ? "Operational" : "Unavailable"}</strong><span>Version {health.version}</span></CardContent></Card><Card className={health.audit_ok ? "status-good" : "status-bad"}><CardContent><span className="status-icon"><ShieldCheck /></span><small>AUDIT CHAIN</small><strong>{health.audit_ok ? "Verified" : "Integrity issue"}</strong><span>{health.stats?.audit ?? 0} linked ledger events</span></CardContent></Card><Card><CardContent><span className="status-icon"><Layers3 /></span><small>ENGAGEMENTS</small><strong>{health.stats?.engagements ?? 0}</strong><span>Tracked in this workspace</span></CardContent></Card></div><Card className="data-card"><CardHeader><CardTitle>Audit ledger</CardTitle><CardDescription>Sequential events with verified chain integrity.</CardDescription></CardHeader><CardContent className="table-card-content"><Table><TableHeader><TableRow><TableHead>Event</TableHead><TableHead>Actor</TableHead><TableHead>Occurred</TableHead><TableHead>Event hash</TableHead></TableRow></TableHeader><TableBody>{(audit.events ?? []).slice().reverse().map((event: AnyRecord, index: number) => <TableRow key={`${event.kind}-${index}`}><TableCell><Badge variant="outline">{titleCase(event.kind)}</Badge></TableCell><TableCell>{event.actor}</TableCell><TableCell>{dateLabel(event.at)} <span className="muted-cell">{new Date(event.at).toLocaleTimeString()}</span></TableCell><TableCell className="mono">{shortHash(event.hash, 18)}</TableCell></TableRow>)}</TableBody></Table></CardContent></Card></div>;
}

function NotFound() {
  return <div className="not-found"><span className="not-found-icon"><FileSearch2 size={24} /></span><p className="eyebrow">404 · NOT ON THE LEDGER</p><h1>We couldn’t find that page.</h1><p>It may have moved, or the link may be incomplete.</p><Button asChild><Link to="/">Back to overview <ArrowLeft /></Link></Button></div>;
}

export default App;
