import type { SourceId } from '../types';
import { SOURCES } from '../utils';
import { TIER_LABEL, entityCaption, entityName, entityTier } from '../terms';

/**
 * 发起端选择器：按 医疗中心 / 医院 两级分组（命名统一取自 terms.ts）。
 * 实体 id 只作为小字技术标识显示。
 */
export default function SourcePicker({
  value, onChange, ids = SOURCES as SourceId[],
}: { value: SourceId; onChange: (s: SourceId) => void; ids?: SourceId[] }) {
  const group = (tier: 'medical' | 'hospital') => {
    const list = ids.filter((id) => entityTier(id) === tier);
    if (!list.length) return null;
    return (
      <div className="src-group" key={tier}>
        <div className="src-group-title">{`${TIER_LABEL[tier]} · ${entityCaption(list[0])}`}</div>
        <div className="src-options">
          {list.map((s) => {
            const on = value === s;
            return (
              <label key={s} className={`src-opt ${on ? 'on' : ''}`}>
                <input type="radio" name="src" value={s} checked={on}
                  onChange={() => onChange(s)} />
                <span className="src-radio" />
                <span className="src-name">{entityName(s)}</span>
                <span className="src-id mono">{s}</span>
                <span className={`src-role ${tier === 'medical' ? 'edge' : 'term'}`}>
                  {TIER_LABEL[tier]}
                </span>
              </label>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="src-picker">
      {group('medical')}
      {group('hospital')}
    </div>
  );
}
