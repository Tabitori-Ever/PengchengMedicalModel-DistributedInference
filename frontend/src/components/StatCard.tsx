interface Props {
  label: string;
  value: string | number;
  hint?: string;
  tone?: 'default' | 'blue' | 'violet' | 'cyan' | 'amber' | 'green' | 'red';
}

export default function StatCard({ label, value, hint, tone = 'default' }: Props) {
  return (
    <div className={`stat-card tone-${tone}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}
