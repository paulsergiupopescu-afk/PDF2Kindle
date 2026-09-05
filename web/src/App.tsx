import { useCallback, useEffect, useRef, useState } from "react";
import type { ConvertOptions, ConvertResult } from "./types";
import { Dropzone } from "./Dropzone";
import { ResultCard } from "./ResultCard";

type Phase = "idle" | "uploading" | "processing" | "done" | "error";

const DEFAULT_OPTS: ConvertOptions = {
  title: "",
  author: "",
  lang: "en",
  ocr: "auto",
  ocr_lang: "eng",
};

export default function App() {
  const [file, setFile] = useState<File | null>(null);
  const [opts, setOpts] = useState<ConvertOptions>(DEFAULT_OPTS);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<ConvertResult | null>(null);
  const [error, setError] = useState("");
  const [ocrAvailable, setOcrAvailable] = useState<boolean | null>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then((d) => setOcrAvailable(!!d.ocr_available))
      .catch(() => setOcrAvailable(null));
  }, []);

  const set = <K extends keyof ConvertOptions>(k: K, v: ConvertOptions[K]) =>
    setOpts((o) => ({ ...o, [k]: v }));

  const reset = () => {
    setFile(null);
    setResult(null);
    setError("");
    setPhase("idle");
    setProgress(0);
  };

  const convert = useCallback(() => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    form.append("title", opts.title);
    form.append("author", opts.author);
    form.append("lang", opts.lang);
    form.append("ocr", opts.ocr);
    form.append("ocr_lang", opts.ocr_lang);

    const xhr = new XMLHttpRequest();
    xhrRef.current = xhr;
    xhr.open("POST", "/api/convert");
    setPhase("uploading");
    setProgress(0);
    setError("");

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        setProgress(pct);
        if (pct >= 100) setPhase("processing");
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        setResult(JSON.parse(xhr.responseText) as ConvertResult);
        setPhase("done");
      } else {
        let detail = "Conversion failed.";
        try {
          detail = JSON.parse(xhr.responseText).detail ?? detail;
        } catch {
          /* ignore */
        }
        setError(detail);
        setPhase("error");
      }
    };
    xhr.onerror = () => {
      setError("Network error — is the server running?");
      setPhase("error");
    };
    xhr.send(form);
  }, [file, opts]);

  const busy = phase === "uploading" || phase === "processing";

  return (
    <div className="app">
      <header className="hero">
        <div className="logo">📖→📱</div>
        <h1>pdf2kindle</h1>
        <p className="tagline">
          Turn PDFs into clean, reflowable Kindle EPUBs — real chapters, justified
          text, and pop-up footnotes.
        </p>
      </header>

      <main className="panel">
        {phase !== "done" && (
          <>
            <Dropzone file={file} disabled={busy} onFile={setFile} />

            <div className="options">
              <div className="row">
                <label>
                  Title <span className="hint">optional</span>
                  <input
                    type="text"
                    placeholder="Auto-detected from PDF"
                    value={opts.title}
                    disabled={busy}
                    onChange={(e) => set("title", e.target.value)}
                  />
                </label>
                <label>
                  Author <span className="hint">optional</span>
                  <input
                    type="text"
                    placeholder="Auto-detected from PDF"
                    value={opts.author}
                    disabled={busy}
                    onChange={(e) => set("author", e.target.value)}
                  />
                </label>
              </div>
              <div className="row">
                <label>
                  Language
                  <input
                    type="text"
                    value={opts.lang}
                    disabled={busy}
                    onChange={(e) => set("lang", e.target.value)}
                  />
                </label>
                <label>
                  OCR scanned pages
                  <select
                    value={opts.ocr}
                    disabled={busy}
                    onChange={(e) => set("ocr", e.target.value as ConvertOptions["ocr"])}
                  >
                    <option value="auto">Auto (recommended)</option>
                    <option value="force">Force on all pages</option>
                    <option value="never">Never</option>
                  </select>
                </label>
                <label>
                  OCR language
                  <input
                    type="text"
                    value={opts.ocr_lang}
                    disabled={busy || opts.ocr === "never"}
                    onChange={(e) => set("ocr_lang", e.target.value)}
                  />
                </label>
              </div>
              {ocrAvailable === false && (
                <p className="warn">
                  ⚠ Tesseract is not installed on the server, so scanned pages
                  can’t be OCR’d. Install <code>tesseract-ocr</code> to enable it.
                </p>
              )}
            </div>

            {phase === "error" && <p className="error">✕ {error}</p>}

            {busy ? (
              <div className="progress">
                <div className="bar">
                  <div
                    className="fill"
                    style={{
                      width: phase === "uploading" ? `${progress}%` : "100%",
                    }}
                  />
                </div>
                <span>
                  {phase === "uploading"
                    ? `Uploading ${progress}%`
                    : "Converting… (analyzing layout, building chapters)"}
                </span>
              </div>
            ) : (
              <button className="convert" disabled={!file} onClick={convert}>
                Convert to EPUB
              </button>
            )}
          </>
        )}

        {phase === "done" && result && (
          <ResultCard result={result} onReset={reset} />
        )}
      </main>

      <footer className="foot">
        Runs entirely on your machine · <code>pdf2kindle serve</code>
      </footer>
    </div>
  );
}
