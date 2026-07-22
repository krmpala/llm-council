import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import {
  displayModelName,
  missingMemberWarning,
  normalizePeerStatuses,
  peerProgress,
  statusLabel,
  uniqueByMemberId,
} from '../councilUi';
import './Stage2.css';

function displayForLabel(label, labelToModel, responsesByMemberId) {
  const memberIdOrModel = labelToModel?.[label];
  if (memberIdOrModel && !responsesByMemberId[memberIdOrModel]) {
    missingMemberWarning(memberIdOrModel);
  }
  return displayModelName(responsesByMemberId[memberIdOrModel] || memberIdOrModel || label);
}

function deAnonymizeText(text, labelToModel, responsesByMemberId) {
  if (!labelToModel) return text;

  let result = text;
  Object.entries(labelToModel).forEach(([label, model]) => {
    result = result.replace(
      new RegExp(label, 'g'),
      `**${displayModelName(responsesByMemberId[model] || model)}**`
    );
  });
  return result;
}

export default function Stage2({
  rankings,
  labelToModel,
  aggregateRankings,
  stage1Responses = [],
  peerStatuses = [],
  peerSummary,
  onRetryPeerEvaluations,
}) {
  const [activeTab, setActiveTab] = useState(0);
  const uniqueRankings = uniqueByMemberId(rankings, 'stage2 evaluators');
  const uniqueStage1Responses = uniqueByMemberId(stage1Responses, 'stage2 response lookup');
  const responsesByMemberId = Object.fromEntries(
    uniqueStage1Responses.map((response) => [response.member_id, response])
  );
  const normalizedPeerStatuses = normalizePeerStatuses(peerStatuses);
  const progress = peerProgress(peerStatuses);

  if ((!uniqueRankings || uniqueRankings.length === 0) && normalizedPeerStatuses.length === 0) {
    return null;
  }
  const activeRanking = uniqueRankings[activeTab];

  return (
    <div className="stage stage2">
      <h3 className="stage-title">Stage 2: Peer Rankings</h3>
      {normalizedPeerStatuses.length > 0 && (
        <div className="stage1-summary">
          {progress.completed} / {progress.total} değerlendirme tamamlandı
        </div>
      )}
      {normalizedPeerStatuses.length > 0 && (
        <div className="model-status-list">
          {normalizedPeerStatuses.map((status) => (
            <div key={status.member_id} className={`model-status ${status.status}`}>
              <div>
                <div className="model-status-name">
                  {displayModelName(responsesByMemberId[status.member_id] || status)}
                </div>
                {(status.error_message || status.http_status) && (
                  <div className="model-error">
                    {status.http_status ? `HTTP ${status.http_status}: ` : ''}
                    {status.error_message}
                  </div>
                )}
              </div>
              <span className="model-status-badge">{statusLabel(status.status)}</span>
            </div>
          ))}
        </div>
      )}
      {peerSummary?.requires_user_choice && (
        <div className="minimum-warning">
          Geçerli değerlendirme üretilemedi.
          {onRetryPeerEvaluations && (
            <button type="button" className="continue-button" onClick={onRetryPeerEvaluations}>
              Değerlendirmeleri yeniden dene
            </button>
          )}
        </div>
      )}

      {uniqueRankings.length === 0 && normalizedPeerStatuses.length > 0 && (
        <div className="minimum-warning">Henüz geçerli değerlendirme yok.</div>
      )}

      {uniqueRankings.length > 0 && <h4>Raw Evaluations</h4>}
      <p className="stage-description">
        Each model evaluated all responses (anonymized as Response A, B, C, etc.) and provided rankings.
        Below, model names are shown in <strong>bold</strong> for readability, but the original evaluation used anonymous labels.
      </p>

      {uniqueRankings.length > 0 && <div className="tabs">
        {uniqueRankings.map((rank, index) => (
          <button
            key={rank.member_id}
            className={`tab ${activeTab === index ? 'active' : ''}`}
            onClick={() => setActiveTab(index)}
          >
            {displayModelName(rank)}
          </button>
        ))}
      </div>}

      {uniqueRankings.length > 0 && <div className="tab-content">
        <div className="ranking-model">
          {activeRanking ? displayModelName(activeRanking) : 'Eşleşmeyen değerlendirme'}
        </div>
        <div className="ranking-content markdown-content">
          {activeRanking ? (
            <ReactMarkdown>
              {deAnonymizeText(
                activeRanking.ranking,
                labelToModel,
                responsesByMemberId
              )}
            </ReactMarkdown>
          ) : (
            <div>Bu değerlendirme için eşleşen member_id bulunamadı.</div>
          )}
        </div>

        {activeRanking?.parsed_ranking &&
         activeRanking.parsed_ranking.length > 0 && (
          <div className="parsed-ranking">
            <strong>Extracted Ranking:</strong>
            <ol>
              {activeRanking.parsed_ranking.map((label) => (
                <li key={label}>
                  {labelToModel && labelToModel[label]
                    ? displayForLabel(label, labelToModel, responsesByMemberId)
                    : label}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>}

      {aggregateRankings && aggregateRankings.length > 0 && (
        <div className="aggregate-rankings">
          <h4>Aggregate Rankings (Street Cred)</h4>
          <p className="stage-description">
            Combined results across all peer evaluations (lower score is better):
          </p>
          <div className="aggregate-list">
            {aggregateRankings.map((agg, index) => (
              <div key={agg.member_id} className="aggregate-item">
                <span className="rank-position">#{index + 1}</span>
                <span className="rank-model">
                  {displayModelName(responsesByMemberId[agg.member_id] || agg)}
                </span>
                <span className="rank-score">
                  Avg: {agg.average_rank.toFixed(2)}
                </span>
                <span className="rank-count">
                  ({agg.rankings_count} votes)
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
