import { useCallback, useRef, useState } from "react";

interface Props {
  file: File | null;
  disabled: boolean;
  onFile: (f: File) => void;
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function Dropzone({ file, disabled, onFile }: Props) {
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const pick = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const f = files[0];
    if (f.name.toLowerCase().endsWith(".pdf")) onFile(f);
  };

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDrag(false);
      if (!disabled) pick(e.dataTransfer.files);
    },
    [disabled],
  );

  return (
    <div
      className={`dropzone${drag ? " drag" : ""}${disabled ? " disabled" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={onDrop}
      onClick={() => !disabled && inputRef.current?.click()}
      role="button"
      tabIndex={0}
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        hidden
        onChange={(e) => pick(e.target.files)}
      />
      {file ? (
        <div className="picked">
          <span className="doc">📄</span>
          <div>
            <strong>{file.name}</strong>
            <span className="size">{humanSize(file.size)}</span>
          </div>
          <span className="change">Change</span>
        </div>
      ) : (
        <div className="prompt">
          <span className="doc">⬆</span>
          <strong>Drop a PDF here</strong>
          <span className="size">or click to browse</span>
        </div>
      )}
    </div>
  );
}
