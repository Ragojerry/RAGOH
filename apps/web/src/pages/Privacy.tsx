/** The privacy notice the collectors accept (so supervisors know what they promised). */
import { get } from "../lib/api";
import { useData } from "../lib/hooks";

interface Privacy {
  notice: { version: string; title: string; sections: { heading: string; body: string }[] };
}

export function PrivacyPage() {
  const p = useData(() => get<Privacy>("/me/privacy"), []);
  const n = p.data?.notice;
  return (
    <div className="page" style={{ maxWidth: 760 }}>
      <h1>{n?.title ?? "Privacy"}</h1>
      <div className="muted">This is what collectors read and accept in the app (version {n?.version ?? "…"}). Using the dashboard means handling their personal data: view and export only what you need for supervision.</div>
      {n?.sections.map((s) => (
        <div className="card" key={s.heading}>
          <h2>{s.heading}</h2>
          <div>{s.body}</div>
        </div>
      ))}
    </div>
  );
}
