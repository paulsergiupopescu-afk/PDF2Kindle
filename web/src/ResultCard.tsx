import type { ConvertResult } from "./types";

interface Props {
  result: ConvertResult;
  onReset: () => void;
}

export function ResultCard({ result, onReset }: Props) {
  const stats: [string, number][] = [
    ["Pages", result.pages],
    ["Chapters", result.chapters],
    ["Footnotes", result.footnotes],
    ["Images", result.images],
  ];
  if (result.ocr_pages > 0) stats.push(["OCR pages", result.ocr_pages]);

  return (
    <div className="result">
      <div className="check">✓</div>
      <h2>Your EPUB is ready</h2>
      <p className="meta">
        <strong>{result.title || "Untitled"}</strong>
        {result.author ? ` · ${result.author}` : ""}
      </p>

      <div className="stats">
        {stats.map(([label, value]) => (
          <div className="stat" key={label}>
            <span className="value">{value}</span>
            <span className="label">{label}</span>
          </div>
        ))}
      </div>

      {result.warnings.length > 0 && (
        <ul className="warnings">
          {result.warnings.map((w, i) => (
            <li key={i}>⚠ {w}</li>
          ))}
        </ul>
      )}

      <div className="actions">
        <a className="download" href={result.download_url} download={result.filename}>
          ⬇ Download {result.filename}
        </a>
        <button className="again" onClick={onReset}>
          Convert another
        </button>
      </div>
    </div>
  );
}
