const concepts = ["10x Chromium", "Drop-seq", "individual cells", "Smart-seq2"];


export function AccessionScope() {
  return (
    <div className="scope-shell" aria-label="Illustration of GEOscope resolving metadata">
      <div className="scope-crosshair" aria-hidden="true" />
      <div className="scope-ring scope-ring--outer" aria-hidden="true" />
      <div className="scope-ring scope-ring--inner" aria-hidden="true" />

      <div className="scope-fragments" aria-hidden="true">
        {concepts.map((concept, index) => (
          <span className={`scope-fragment scope-fragment--${index + 1}`} key={concept}>
            {concept}
          </span>
        ))}
      </div>

      <div className="scope-focus">
        <span className="scope-focus__eyebrow">CONCEPT RESOLVED</span>
        <strong>scRNA-seq</strong>
        <span className="scope-focus__id">assay label</span>
      </div>

      <div className="scope-hit scope-hit--one">
        <span>01</span>
        <strong>GSE240813</strong>
      </div>
      <div className="scope-hit scope-hit--two">
        <span>02</span>
        <strong>GSE184880</strong>
      </div>
    </div>
  );
}
