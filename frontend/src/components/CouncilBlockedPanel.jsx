import { continueButtonLabel, displayModelName } from '../councilUi';
import './CouncilBlockedPanel.css';

export default function CouncilBlockedPanel({
  summary,
  statuses = [],
  onContinue,
  isLoading,
}) {
  if (!summary?.blocked) return null;

  const failedModels = statuses.filter((status) => status.status === 'failed');
  const buttonLabel = continueButtonLabel(summary);

  return (
    <div className="council-blocked-panel">
      <h3>Konsey duraklatıldı</h3>
      <p>
        {summary.successful} model başarılı oldu. Gerekli minimum katılımcı
        sayısı: {summary.minimum_required}.
      </p>

      {failedModels.length > 0 && (
        <div className="blocked-failures">
          <h4>Başarısız modeller</h4>
          {failedModels.map((status) => (
            <div key={status.member_id || status.model} className="blocked-failure">
              <div className="blocked-failure-name">{displayModelName(status)}</div>
              <div className="blocked-failure-meta">
                {status.error?.http_status ? `HTTP ${status.error.http_status}` : 'HTTP kodu yok'}
                <span>Deneme: {status.attempts || 0}</span>
              </div>
              {status.error?.message && (
                <div className="blocked-failure-error">{status.error.message}</div>
              )}
            </div>
          ))}
        </div>
      )}

      {buttonLabel ? (
        <div className="blocked-actions">
          <p>Stage 2 ve Stage 3 kullanıcı onayı olmadan başlamaz.</p>
          <button
            type="button"
            className="continue-button"
            onClick={onContinue}
            disabled={isLoading}
          >
            {buttonLabel}
          </button>
        </div>
      ) : (
        <div className="blocked-actions">
          Yeni bir soru göndermek veya model/fallback yapılandırmasını değiştirmek gerekir.
        </div>
      )}
    </div>
  );
}
