export const STATUS_LABELS = {
  pending: 'Bekliyor',
  answering: 'Cevaplıyor',
  running: 'Değerlendiriyor',
  completed: 'Tamamlandı',
  retrying: 'Yeniden deneniyor',
  failed: 'Başarısız',
  invalid_output: 'Geçersiz çıktı',
  interrupted: 'Kesintiye uğradı',
};

export function displayModelName(item) {
  if (!item) return '';
  const model = typeof item === 'string'
    ? item
    : item.actual_model || item.model || item.primary_model;
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

export function modelFailureDetails(status) {
  const rawError = status?.error;
  const error = rawError && typeof rawError === 'object' ? rawError : {};
  const errorType = error.type || status?.error_type || null;
  const rawMessage = error.message || status?.error_message || (
    typeof rawError === 'string' ? rawError : ''
  );
  const looksLikeChoicesKeyError = /^['"]?choices['"]?$/.test(String(rawMessage).trim());
  const messages = {
    missing_choices: 'Model saglayicisinin yanitinda choices alani bulunamadi.',
    empty_choices: 'Model saglayicisinin yanitinda choices listesi bos.',
    missing_message: 'Model saglayicisinin yanitinda message alani bulunamadi.',
    missing_content: 'Model saglayicisinin yanitinda metin icerigi bulunamadi.',
    empty_content: 'Model saglayicisi bos bir metin icerigi dondu.',
    quality_validation_failed: 'Model hedef dilde kullanilabilir bir yanit uretemedi.',
  };
  return {
    httpStatus: error.http_status ?? status?.http_status ?? null,
    errorType: looksLikeChoicesKeyError ? 'missing_choices' : errorType,
    message: looksLikeChoicesKeyError
      ? messages.missing_choices
      : messages[errorType] || rawMessage || 'Model yaniti kontrollu bicimde basarisiz oldu.',
    attempts: status?.attempts || 0,
    primaryModel: status?.primary_model || status?.model || null,
    actualModel: status?.actual_model || status?.model || null,
    fallbackUsed: Boolean(status?.used_fallback),
    fallbackIndex: status?.fallback_index ?? null,
  };
}

export function upsertModelStatus(statuses = [], nextStatus) {
  const index = statuses.findIndex((status) => {
    if (nextStatus.member_id && status.member_id) {
      return status.member_id === nextStatus.member_id;
    }
    return status.model === nextStatus.model;
  });
  if (index === -1) return [...statuses, nextStatus];

  const updated = [...statuses];
  updated[index] = nextStatus;
  return updated;
}

export function isCouncilBlockedMessage(message) {
  return Boolean(message?.metadata?.stage1_summary?.blocked && !message?.stage3);
}

export function shouldRenderCouncilStages(message) {
  return !isCouncilBlockedMessage(message);
}

export function continueButtonLabel(summary) {
  if (!summary?.override_available || summary.successful < 2) return null;
  return `Kalan ${summary.successful} üyeyle devam et`;
}

export function uniqueByMemberId(items = [], label = 'items') {
  const seen = new Set();
  const unique = [];
  items.forEach((item) => {
    if (!item?.member_id) {
      console.warn(`[llm-council invariant] ${label} item missing member_id; dropped`);
      return;
    }
    if (seen.has(item.member_id)) {
      console.warn(`[llm-council invariant] duplicate member_id in ${label}: ${item.member_id}; dropped`);
      return;
    }
    seen.add(item.member_id);
    unique.push(item);
  });
  return unique;
}

export function missingMemberWarning(memberId) {
  console.warn(`[llm-council invariant] missing member_id match: ${memberId}`);
}

export function isChairmanFailed(finalResponse) {
  return finalResponse?.status === 'failed';
}

export function chairmanErrorDetails(finalResponse) {
  if (!isChairmanFailed(finalResponse)) return null;
  return {
    stage: finalResponse.stage || 'bilinmiyor',
    httpStatus: finalResponse.http_status || null,
    attempts: finalResponse.attempts || 0,
    message: finalResponse.error_message || 'Başkan yanıtı üretilemedi.',
  };
}

export const PEER_TERMINAL_STATUSES = new Set([
  'completed',
  'failed',
  'invalid_output',
  'interrupted',
]);

export function normalizePeerStatuses(statuses = []) {
  return uniqueByMemberId(statuses.map((status) => {
    if (status.status === 'running' || status.status === 'retrying' || status.status === 'pending') {
      return {
        ...status,
        status: 'interrupted',
        error_message: status.error_message || 'Değerlendirme kesintiye uğradı.',
      };
    }
    return status;
  }), 'peer evaluator statuses');
}

export function peerProgress(statuses = []) {
  const normalized = normalizePeerStatuses(statuses);
  return {
    completed: normalized.filter((status) => PEER_TERMINAL_STATUSES.has(status.status)).length,
    total: normalized.length,
    terminal: normalized.every((status) => PEER_TERMINAL_STATUSES.has(status.status)),
  };
}
