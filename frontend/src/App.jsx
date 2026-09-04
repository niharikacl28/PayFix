import { useEffect, useState } from "react";
import { Icon } from "./Icon.jsx";
import Dashboard from "./Dashboard.jsx";
import PaymentDetail from "./PaymentDetail.jsx";
import { api } from "./api.js";

function Sidebar({ activeView, onChange }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" />
        <div className="brand-text">
          <strong>PayFix</strong>
          <span>AI Revenue Recovery</span>
        </div>
      </div>
      <nav className="nav">
        <span className="nav-section-title">Workspace</span>
        <button
          className={`nav-link ${activeView === "dashboard" ? "active" : ""}`}
          onClick={() => onChange("dashboard")}
        >
          <Icon name="dashboard" /> Dashboard
        </button>
        <button
          className={`nav-link ${activeView === "queue" ? "active" : ""}`}
          onClick={() => onChange("queue")}
        >
          <Icon name="queue" /> Recovery Queue
        </button>
        <span className="nav-section-title">Operations</span>
        <button
          className={`nav-link ${activeView === "ai" ? "active" : ""}`}
          onClick={() => onChange("ai")}
        >
          <Icon name="ai" /> AI Decisions
        </button>
        <button
          className={`nav-link ${activeView === "guardrails" ? "active" : ""}`}
          onClick={() => onChange("guardrails")}
        >
          <Icon name="shield" /> Guardrails
        </button>
      </nav>
      <div className="sidebar-footer">
        <strong>Simulation mode</strong>
        PayFix runs entirely in simulation.
        No real payments are processed and no customer communications are sent.
      </div>
    </aside>
  );
}

function HealthPill() {
  const [status, setStatus] = useState("checking");
  useEffect(() => {
    let cancelled = false;
    api.health()
      .then(() => { if (!cancelled) setStatus("ok"); })
      .catch(() => { if (!cancelled) setStatus("down"); });
    return () => { cancelled = true; };
  }, []);
  return (
    <div className="sim-notice" title="Backend health">
      <span className="sim-dot" />
      Backend {status === "ok" ? "live" : status === "down" ? "unreachable" : "checking…"}
    </div>
  );
}

function Header({ title, subtitle, right }) {
  return (
    <header className="page-header">
      <div>
        <span className="eyebrow">Razorpay Buildathon · Track 03</span>
        <h1>{title}</h1>
        {subtitle ? <div className="subtitle">{subtitle}</div> : null}
      </div>
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <HealthPill />
        {right}
      </div>
    </header>
  );
}

function ComingSoon({ title, message }) {
  return (
    <div className="state" style={{ minHeight: 360 }}>
      <div className="icon"><Icon name="ai" size={18} /></div>
      <strong>{title}</strong>
      <span>{message}</span>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState("dashboard");
  const [selectedPayment, setSelectedPayment] = useState(null);
  const [initialDecision, setInitialDecision] = useState(null);

  const goDashboard = () => {
    setSelectedPayment(null);
    setInitialDecision(null);
    setView("dashboard");
  };

  const selectPayment = (paymentId, decision) => {
    setSelectedPayment(paymentId);
    setInitialDecision(decision ?? null);
  };

  const renderMain = () => {
    if (selectedPayment) {
      return (
        <PaymentDetail
          paymentId={selectedPayment}
          initialDecision={initialDecision}
          onBack={goDashboard}
        />
      );
    }
    if (view === "dashboard") {
      return <Dashboard onSelectPayment={selectPayment} />;
    }
    if (view === "queue") {
      return (
        <>
          <Header
            title="Recovery queue"
            subtitle="Representative cases from the backend demo dataset. Click any row to see the full AI decision."
            right={<span className="sim-notice"><span className="sim-dot" />Simulation mode</span>}
          />
          <ComingSoon
            title="Recovery queue"
            message="The same cases shown on the dashboard are also accessible here. Use the Dashboard to browse."
          />
        </>
      );
    }
    if (view === "ai") {
      return (
        <>
          <Header
            title="AI decisions"
            subtitle="Diagnoses, simulations, and final actions taken by PayFix across the representative cases."
            right={<span className="sim-notice"><span className="sim-dot" />Simulation mode</span>}
          />
          <ComingSoon
            title="AI decisions"
            message="Open any payment from the dashboard to inspect its full AI decision in detail."
          />
        </>
      );
    }
    if (view === "guardrails") {
      return (
        <>
          <Header
            title="Guardrails"
            subtitle="The deterministic safety checks every PayFix decision passes through before execution."
            right={<span className="sim-notice"><span className="sim-dot" />Simulation mode</span>}
          />
          <ComingSoon
            title="Guardrails"
            message="Open any payment to see the guardrail checklist for that specific recovery action."
          />
        </>
      );
    }
    return null;
  };

  return (
    <div className="app">
      <Sidebar activeView={view} onChange={(v) => { setSelectedPayment(null); setView(v); }} />
      <main className="main">{renderMain()}</main>
    </div>
  );
}
