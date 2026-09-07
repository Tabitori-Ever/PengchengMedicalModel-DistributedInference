import type { SourceId } from '../types';

/**
 * v3 task source picker — radio-style buttons grouped by 边(hospital) / 端(clinic).
 */
export default function SourcePicker({
  value, onChange,
}: { value: SourceId; onChange: (s: SourceId) => void }) {
  const group = (title: string, role: '边' | '端', ids: SourceId[]) => (
    <div className="src-group" key={role}>
      <div className="src-group-title">{title}</div>
      <div className="src-options">
        {ids.map((s) => {
          const on = value === s;
          return (
            <label key={s} className={`src-opt ${on ? 'on' : ''}`}>
              <input type="radio" name="src" value={s} checked={on}
                onChange={() => onChange(s)} />
              <span className="src-radio" />
              <span className="src-name">{s}</span>
              <span className={`src-role ${role === '边' ? 'edge' : 'term'}`}>{role}</span>
            </label>
          );
        })}
      </div>
    </div>
  );

  return (
    <div className="src-picker">
      {group('医院 Pod · 边 edge', '边', ['hospital-a', 'hospital-b'])}
      {group('诊所 Pod · 端 terminal', '端', ['clinic-1', 'clinic-2'])}
    </div>
  );
}
