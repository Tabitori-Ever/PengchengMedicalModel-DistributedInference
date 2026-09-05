import type { ModelKind } from '../types';

export default function Badge({ model, status }: { model?: string; status?: string }) {
  const cls = ['badge'];
  if (model) cls.push(`m-${model}`);
  if (status) cls.push(`s-${status}`);
  return <span className={cls.join(' ')}>{model ? MODEL_TEXT[model] : ''}{status ? STATUS_TEXT[status] : ''}</span>;
}

const MODEL_TEXT: Record<string, string> = {
  medical: '医疗',
  alexnet: '分类',
  clinic: '内存',
};
const STATUS_TEXT: Record<string, string> = {
  running: '运行中',
  queued: '排队',
  finished: '完成',
  completed: '完成',
  failed: '失败',
};

export type { ModelKind };
