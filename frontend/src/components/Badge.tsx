import { MODEL_LABEL, STATUS_LABEL } from '../utils';

export default function Badge({ model, status }: { model?: string; status?: string }) {
  const cls = ['badge'];
  if (model) cls.push(`m-${model}`);
  if (status) cls.push(`s-${status}`);
  return (
    <span className={cls.join(' ')}>
      {model ? MODEL_LABEL[model] || model : ''}
      {status ? STATUS_LABEL[status] || status : ''}
    </span>
  );
}
