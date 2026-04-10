import { useEffect, useMemo, useState } from "react";

const API_BASE = process.env.REACT_APP_API_BASE_URL || "";

const isEmpty = (v) => v === "" || v === null || v === undefined || (Array.isArray(v) && v.length === 0);
const showField = (field, data) => {
  const c = field.conditional_on;
  return !c || data[c.field] === c.value;
};
const titleize = (v = "") => v.replaceAll("_", " ").split(" ").filter(Boolean).map((x) => x[0].toUpperCase() + x.slice(1)).join(" ");

function initForm(schema, old = {}) {
  const out = {};
  for (const s of schema?.sections || []) {
    for (const f of s.fields || []) {
      const prev = old[f.id];
      if (!isEmpty(prev)) out[f.id] = prev;
      else if (f.type === "boolean") out[f.id] = false;
      else if (f.type === "multiselect") out[f.id] = [];
      else out[f.id] = "";
    }
  }
  return out;
}

function payloadFrom(schema, data) {
  const out = {};
  for (const s of schema?.sections || []) {
    for (const f of s.fields || []) {
      if (!showField(f, data)) continue;
      const v = data[f.id];
      if (isEmpty(v)) continue;
      if (f.type === "number") {
        const n = Number(v);
        if (!Number.isNaN(n)) out[f.id] = n;
      } else out[f.id] = v;
    }
  }
  return out;
}

function App() {
  const [entered, setEntered] = useState(false);
  const [step, setStep] = useState(1);
  const [types, setTypes] = useState([]);
  const [backends, setBackends] = useState([]);
  const [docType, setDocType] = useState("");
  const [backend, setBackend] = useState("");
  const [schema, setSchema] = useState(null);
  const [form, setForm] = useState({});
  const [loadingBoot, setLoadingBoot] = useState(true);
  const [loadingGenerate, setLoadingGenerate] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    (async () => {
      setLoadingBoot(true);
      setError("");
      try {
        const [a, b] = await Promise.all([fetch(`${API_BASE}/document-types`), fetch(`${API_BASE}/backends`)]);
        if (!a.ok || !b.ok) throw new Error("Unable to fetch backend metadata");
        const ta = await a.json();
        const tb = await b.json();
        const loadedTypes = ta.document_types || [];
        const loadedBackends = tb.backends || [];
        setTypes(loadedTypes);
        setBackends(loadedBackends);
        setDocType(loadedTypes[0] || "");
        setBackend(loadedBackends[0] || "");
      } catch (e) {
        setError(e.message || "Backend connection failed");
      } finally {
        setLoadingBoot(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!docType) return;
    (async () => {
      setError("");
      setResult(null);
      try {
        const r = await fetch(`${API_BASE}/intake-schema/${docType}`);
        if (!r.ok) throw new Error(`Schema not found for ${docType}`);
        const s = await r.json();
        setSchema(s);
        setForm((old) => initForm(s, old));
      } catch (e) {
        setError(e.message || "Schema load failed");
        setSchema(null);
      }
    })();
  }, [docType]);

  const missing = useMemo(() => {
    const miss = [];
    for (const s of schema?.sections || []) {
      for (const f of s.fields || []) {
        if (!f.required || !showField(f, form)) continue;
        if (isEmpty(form[f.id])) miss.push(f.id);
      }
    }
    return miss;
  }, [schema, form]);

  const progress = useMemo(() => {
    let req = 0;
    for (const s of schema?.sections || []) {
      for (const f of s.fields || []) {
        if (f.required && showField(f, form)) req += 1;
      }
    }
    if (req === 0) return 0;
    return Math.round(((req - missing.length) / req) * 100);
  }, [schema, form, missing.length]);

  const updateField = (id, value) => setForm((old) => ({ ...old, [id]: value }));

  const generate = async () => {
    if (missing.length > 0) return;
    setLoadingGenerate(true);
    setError("");
    setResult(null);
    try {
      const r = await fetch(`${API_BASE}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          document_type: docType,
          user_data: payloadFrom(schema, form),
          backend,
          pipeline_variant: "CAG",
          export_files: true,
        }),
      });
      const p = await r.json();
      if (!r.ok) throw new Error(typeof p.detail === "string" ? p.detail : "Generation failed");
      setResult(p);
      setStep(4);
    } catch (e) {
      setError(e.message || "Generation failed");
    } finally {
      setLoadingGenerate(false);
    }
  };

  const renderField = (f) => {
    if (!showField(f, form)) return null;
    const value = form[f.id];
    const invalid = f.required && missing.includes(f.id);

    return (
      <div className="mb-3" key={f.id}>
        {f.type !== "boolean" && (
          <label className="mb-1 block text-sm font-semibold text-premium-text">
            {f.label}
            {f.required ? <span className="text-red-700"> *</span> : null}
          </label>
        )}

        {f.type === "textarea" ? (
          <textarea className={`control min-h-[90px] ${invalid ? "border-red-300 bg-red-50" : ""}`} value={value || ""} onChange={(e) => updateField(f.id, e.target.value)} />
        ) : null}

        {f.type === "select" ? (
          <select className={`control ${invalid ? "border-red-300 bg-red-50" : ""}`} value={value || ""} onChange={(e) => updateField(f.id, e.target.value)}>
            <option value="">Select...</option>
            {(f.options || []).map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        ) : null}

        {f.type === "multiselect" ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(f.options || []).map((opt) => {
              const selected = Array.isArray(value) ? value : [];
              const checked = selected.includes(opt);
              return (
                <label key={opt} className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-sm ${checked ? "border-premium-primary bg-[#fff2e8]" : "border-premium-line bg-white"}`}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(e) => {
                      if (e.target.checked) updateField(f.id, [...selected, opt]);
                      else updateField(f.id, selected.filter((x) => x !== opt));
                    }}
                  />
                  <span>{opt}</span>
                </label>
              );
            })}
          </div>
        ) : null}

        {f.type === "boolean" ? (
          <label className="flex items-center gap-3 rounded-xl border border-premium-line bg-white px-3 py-2 text-sm">
            <input type="checkbox" checked={Boolean(value)} onChange={(e) => updateField(f.id, e.target.checked)} />
            <span>{f.label}</span>
          </label>
        ) : null}

        {(f.type === "text" || f.type === "number" || f.type === "date" || !["textarea", "select", "multiselect", "boolean"].includes(f.type)) ? (
          <input
            className={`control ${invalid ? "border-red-300 bg-red-50" : ""}`}
            type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"}
            value={value || ""}
            onChange={(e) => updateField(f.id, e.target.value)}
          />
        ) : null}
      </div>
    );
  };

  if (!entered) {
    return (
      <div className="grid min-h-screen place-items-center p-4">
        <div className="card-premium w-full max-w-3xl px-8 py-10 text-center">
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-[#8e5639]">Maharashtra Legal Drafting Suite</p>
          <h1 className="mt-3 font-display text-3xl leading-tight text-premium-text sm:text-4xl">Maharashtra Legal Document Generation System</h1>
          <p className="mx-auto mt-4 max-w-2xl text-premium-muted">Tailwind-powered premium workspace for intake, generation, review, and export.</p>
          {loadingBoot ? <div className="mx-auto mt-5 max-w-md rounded-xl border border-[#f2d19f] bg-[#fff8ea] px-4 py-2 text-sm text-[#8e5836]">Connecting to backend...</div> : null}
          {error ? <div className="mx-auto mt-5 max-w-xl rounded-xl border border-[#efbbb3] bg-[#ffece9] px-4 py-2 text-sm text-premium-danger">{error}</div> : null}
          <button className="btn-primary mt-7" disabled={loadingBoot || !!error} onClick={() => setEntered(true)}>Enter Workspace</button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <header className="card-premium flex flex-col gap-3 px-5 py-5 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="font-display text-2xl text-premium-text">Legal Draft Workspace</h2>
          <p className="mt-1 text-sm text-premium-muted">Configure model, complete intake, and generate final draft.</p>
        </div>
        <div className="inline-flex items-center rounded-full border border-[#f0cba9] bg-[#fff3e9] px-4 py-2 text-sm font-semibold text-[#8f4b2f]">Form Completion: {progress}%</div>
      </header>

      {error ? <div className="mt-4 rounded-xl border border-[#efbbb3] bg-[#ffece9] px-4 py-2 text-sm text-premium-danger">{error}</div> : null}

      <nav className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
        {["Configuration", "Fill Details", "Review", "Output"].map((s, i) => (
          <button
            key={s}
            className={`rounded-xl border px-3 py-2 text-sm font-semibold transition ${step === i + 1 ? "border-premium-primary bg-gradient-to-r from-premium-primary to-premium-primaryDark text-white" : "border-premium-line bg-premium-surface text-premium-muted hover:bg-[#f9f3eb]"}`}
            onClick={() => setStep(i + 1)}
            disabled={(i + 1) > 2 && !schema}
          >
            {s}
          </button>
        ))}
      </nav>

      {step === 1 ? (
        <section className="card-premium mt-4 p-5">
          <h3 className="font-display text-xl text-premium-text">Configuration</h3>
          <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-semibold">Document Type</label>
              <select className="control" value={docType} onChange={(e) => setDocType(e.target.value)}>
                {types.map((t) => <option key={t} value={t}>{titleize(t)}</option>)}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-semibold">LLM Backend</label>
              <select className="control" value={backend} onChange={(e) => setBackend(e.target.value)}>
                {backends.map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
            </div>
          </div>
          <div className="mt-5 flex justify-end">
            <button className="btn-primary" onClick={() => setStep(2)}>Continue</button>
          </div>
        </section>
      ) : null}

      {step === 2 ? (
        <section className="card-premium mt-4 p-5">
          <h3 className="font-display text-xl text-premium-text">Fill Details</h3>
          <div className="mt-4 space-y-3">
            {schema?.sections?.map((section) => (
              <details className="overflow-hidden rounded-2xl border border-[#efdfcf]" key={section.title} open>
                <summary className="cursor-pointer bg-[#fff8ef] px-4 py-3 text-sm font-bold text-[#683d2a]">{section.title}</summary>
                <div className="bg-white px-4 py-4">{section.fields.map((f) => renderField(f))}</div>
              </details>
            ))}
          </div>
          <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:justify-between">
            <button className="btn-ghost" onClick={() => setStep(1)}>Back</button>
            <button className="btn-primary" onClick={() => setStep(3)}>Review</button>
          </div>
        </section>
      ) : null}

      {step === 3 ? (
        <section className="card-premium mt-4 p-5">
          <h3 className="font-display text-xl text-premium-text">Review</h3>
          <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
            {Object.entries(form).map(([k, v]) => (
              <div className="rounded-xl border border-[#eadbcc] bg-[#fffaf3] p-3" key={k}>
                <span className="block text-xs font-semibold uppercase tracking-wide text-premium-muted">{titleize(k)}</span>
                <strong className="mt-1 block text-sm font-semibold">{Array.isArray(v) ? v.join(", ") : String(v || "-")}</strong>
              </div>
            ))}
          </div>
          {missing.length > 0 ? <div className="mt-4 rounded-xl border border-[#f0cb8d] bg-[#fff3e2] px-4 py-2 text-sm text-[#875013]">Missing required fields: {missing.map(titleize).join(", ")}</div> : null}
          <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:justify-between">
            <button className="btn-ghost" onClick={() => setStep(2)}>Back</button>
            <button className="btn-primary" disabled={missing.length > 0 || loadingGenerate} onClick={generate}>{loadingGenerate ? "Generating..." : "Generate Document"}</button>
          </div>
        </section>
      ) : null}

      {step === 4 ? (
        <section className="card-premium mt-4 p-5">
          <h3 className="font-display text-xl text-premium-text">Output</h3>
          {result ? (
            <>
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-sm text-premium-muted">
                <span>Run ID: <strong className="text-premium-text">{result.run_id}</strong></span>
                <span>Model: <strong className="text-premium-text">{result.backend}</strong></span>
                <span>Citations: <strong className="text-premium-text">{result.citations?.length || 0}</strong></span>
              </div>
              <pre className="mt-4 max-h-[460px] overflow-auto whitespace-pre-wrap rounded-2xl border border-[#e4d2bf] bg-[#fffefa] p-4 font-display text-sm leading-7">{result.output}</pre>
              <div className="mt-4 flex flex-wrap gap-2">
                <a className={`btn-secondary ${result.export?.docx_url ? "" : "pointer-events-none opacity-60"}`} href={result.export?.docx_url ? `${API_BASE}${result.export.docx_url}` : "#"}>Download DOCX</a>
                <a className={`btn-secondary ${result.export?.pdf_url ? "" : "pointer-events-none opacity-60"}`} href={result.export?.pdf_url ? `${API_BASE}${result.export.pdf_url}` : "#"}>Download PDF</a>
              </div>
              {result.export?.errors?.length ? (
                <div className="mt-4 rounded-xl border border-[#f0cb8d] bg-[#fff3e2] px-4 py-3 text-sm text-[#875013]">
                  {result.export.errors.join(" | ")}
                </div>
              ) : null}
            </>
          ) : (
            <p className="mt-3 text-sm text-premium-muted">No generated document available yet.</p>
          )}
        </section>
      ) : null}
    </div>
  );
}

export default App;
