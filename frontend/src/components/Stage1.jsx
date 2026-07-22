import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { displayModelName, statusLabel } from '../councilUi';
import './Stage1.css';

export default function Stage1({ responses = [], statuses = [], summary }) {
  const [activeTab, setActiveTab] = useState(0);

  if ((!responses || responses.length === 0) && (!statuses || statuses.length === 0)) {
    return null;
  }

  const activeResponse = responses[activeTab];

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
        {statuses.map((status) => (
          <div key={status.model} className={`model-status ${status.status}`}>
            <div>
              <div className="model-status-name">{displayModelName(status)}</div>
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

      {responses.length > 0 && (
        <>
          <div className="tabs">
            {responses.map((resp, index) => (
              <button
                key={resp.model}
                className={`tab ${activeTab === index ? 'active' : ''}`}
                onClick={() => setActiveTab(index)}
              >
                {displayModelName(resp)}
              </button>
            ))}
          </div>

          <div className="tab-content">
            <div className="model-name">{displayModelName(activeResponse)}</div>
            <div className="response-text markdown-content">
              <ReactMarkdown>{activeResponse.response}</ReactMarkdown>
            </div>
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
