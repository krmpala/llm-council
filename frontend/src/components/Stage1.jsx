import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import {
  displayModelName,
  statusLabel,
  uniqueByMemberId,
} from '../councilUi';
import './Stage1.css';

export default function Stage1({ responses = [], statuses = [], summary }) {
  const [activeTab, setActiveTab] = useState(0);

  if ((!responses || responses.length === 0) && (!statuses || statuses.length === 0)) {
    return null;
  }

  const uniqueResponses = uniqueByMemberId(responses, 'stage1 responses');
  const uniqueStatuses = uniqueByMemberId(statuses, 'stage1 statuses');
  const activeResponse = uniqueResponses[activeTab];

  return (
    <div className="stage stage1">
      <h3 className="stage-title">Stage 1: Individual Responses</h3>

      {summary && (
        <div className="stage1-summary">
          {summary.successful} / {summary.total} model başarıyla cevapladı.
          <span> Minimum: {summary.minimum_required}</span>
        </div>
      )}

      <div className="model-status-list">
        {uniqueStatuses.map((status) => (
          <div key={status.member_id} className={`model-status ${status.status}`}>
            <div>
              <div className="model-status-name">{displayModelName(status)}</div>
              {status.used_fallback && (
                <div className="fallback-note">
                  Fallback kullanıldı: {displayModelName(status)}
                </div>
              )}
              {status.error && (
                <div className="model-error">
                  {status.error.http_status ? `HTTP ${status.error.http_status}: ` : ''}
                  {status.error.message}
                </div>
              )}
            </div>
            <span className="model-status-badge">{statusLabel(status.status)}</span>
          </div>
        ))}
      </div>

      {uniqueResponses.length > 0 && (
        <>
          <div className="tabs">
            {uniqueResponses.map((resp, index) => (
              <button
                key={resp.member_id}
                className={`tab ${activeTab === index ? 'active' : ''}`}
                onClick={() => setActiveTab(index)}
              >
                {displayModelName(resp)}
              </button>
            ))}
          </div>

          <div className="tab-content">
            {activeResponse ? (
              <>
                <div className="model-name">{displayModelName(activeResponse)}</div>
                {activeResponse.truncated && (
                  <div className="minimum-warning">Yanıt token sınırında kesildi.</div>
                )}
                <div className="response-text markdown-content">
                  <ReactMarkdown>{activeResponse.response}</ReactMarkdown>
                </div>
              </>
            ) : (
              <div className="minimum-warning">
                Bu sekme için eşleşen member_id bulunamadı.
              </div>
            )}
          </div>
        </>
      )}

      {summary?.blocked && (
        <div className="minimum-warning">
          {summary.message}
        </div>
      )}
    </div>
  );
}
