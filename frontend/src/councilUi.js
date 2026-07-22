export const STATUS_LABELS = {
  pending: 'Bekliyor',
  answering: 'Cevaplıyor',
  completed: 'Tamamlandı',
  retrying: 'Yeniden deneniyor',
  failed: 'Başarısız',
};

export function displayModelName(item) {
  if (!item) return '';
  const model = typeof item === 'string' ? item : item.model;
  const provider = typeof item === 'string'
    ? model?.split('/')[0]
    : item.provider || model?.split('/')[0];
  const modelName = typeof item === 'string'
    ? model?.split('/')[1]
    : item.model_name || model?.split('/')[1] || model;

  return `${provider || 'unknown'} / ${modelName || model || ''}`;
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || status;
}

export function upsertModelStatus(statuses = [], nextStatus) {
  const index = statuses.findIndex((status) => status.model === nextStatus.model);
  if (index === -1) return [...statuses, nextStatus];

  const updated = [...statuses];
  updated[index] = nextStatus;
  return updated;
}
