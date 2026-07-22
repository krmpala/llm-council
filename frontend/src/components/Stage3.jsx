import ReactMarkdown from 'react-markdown';
import { useState } from 'react';
import {
  chairmanErrorDetails,
  displayModelName,
  isChairmanFailed,
  uniqueByMemberId,
} from '../councilUi';
import './Stage3.css';

function deAnonymizeText(text, labelToModel, responsesByMemberId) {
  if (!labelToModel) return text;

  let result = text;
  Object.entries(labelToModel).forEach(([label, memberId]) => {
    result = result.replace(
      new RegExp(label, 'g'),
      `**${displayModelName(responsesByMemberId[memberId] || memberId)}**`
    );
  });
  return result;
}

export default function Stage3({
  finalResponse,
  stage1Responses = [],
  stage2Rankings = [],
  labelToModel,
  onRetryChairman,
  isLoading,
}) {
  const [activeTab, setActiveTab] = useState('common');
  const uniqueStage1Responses = uniqueByMemberId(stage1Responses, 'stage3 answers');
  const uniqueStage2Rankings = uniqueByMemberId(stage2Rankings, 'stage3 reviews');
  const responsesByMemberId = Object.fromEntries(
    uniqueStage1Responses.map((response) => [response.member_id, response])
  );
  const chairmanError = chairmanErrorDetails(finalResponse);

  if (!finalResponse) {
    return null;
  }

  return (
    <div className="stage stage3">
      <h3 className="stage-title">Stage 3: Final Council Answer</h3>

      <div className="final-tabs">
        <button
          type="button"
          className={`final-tab ${activeTab === 'common' ? 'active' : ''}`}
          onClick={() => setActiveTab('common')}
        >
          Ortak Cevap
        </button>
        <button
          type="button"
          className={`final-tab ${activeTab === 'answers' ? 'active' : ''}`}
          onClick={() => setActiveTab('answers')}
        >
          Ayrı Cevaplar
        </button>
        <button
          type="button"
          className={`final-tab ${activeTab === 'reviews' ? 'active' : ''}`}
          onClick={() => setActiveTab('reviews')}
        >
          Değerlendirmeler
        </button>
      </div>

      <div className="final-response">
        {activeTab === 'common' && (
          <>
            <div className="chairman-label">
              Chairman: {displayModelName(finalResponse)}
            </div>
            {isChairmanFailed(finalResponse) ? (
              <div className="chairman-error">
                <strong>Başkan yanıtı üretilemedi.</strong>
                <div>Aşama: {chairmanError.stage}</div>
                {chairmanError.httpStatus && <div>HTTP: {chairmanError.httpStatus}</div>}
                <div>Deneme: {chairmanError.attempts}</div>
                {finalResponse.error_type && <div>Hata türü: {finalResponse.error_type}</div>}
                <div>{chairmanError.message}</div>
                <button
                  type="button"
                  className="retry-chairman-button"
                  onClick={onRetryChairman}
                  disabled={isLoading}
                >
                  Başkanı yeniden dene
                </button>
              </div>
            ) : (
              <>
                {finalResponse.quality_warning && (
                  <div className="quality-warning">{finalResponse.quality_warning}</div>
                )}
                <div className="final-text markdown-content">
                  <ReactMarkdown>{finalResponse.response}</ReactMarkdown>
                </div>
              </>
            )}
          </>
        )}

        {activeTab === 'answers' && (
          <div className="final-list">
            {uniqueStage1Responses.length === 0 ? (
              <p className="empty-tab">Ayrı cevap yok.</p>
            ) : (
              uniqueStage1Responses.map((response) => (
                <div key={response.member_id} className="final-list-item">
                  <div className="final-list-title">{displayModelName(response)}</div>
                  {response.truncated && (
                    <div className="quality-warning">Yanıt token sınırında kesildi.</div>
                  )}
                  <div className="markdown-content">
                    <ReactMarkdown>{response.response}</ReactMarkdown>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {activeTab === 'reviews' && (
          <div className="final-list">
            {uniqueStage2Rankings.length === 0 ? (
              <p className="empty-tab">Değerlendirme yok.</p>
            ) : (
              uniqueStage2Rankings.map((ranking) => (
                <div key={ranking.member_id} className="final-list-item">
                  <div className="final-list-title">{displayModelName(ranking)}</div>
                  <div className="markdown-content">
                    <ReactMarkdown>
                      {deAnonymizeText(
                        ranking.ranking,
                        labelToModel,
                        responsesByMemberId
                      )}
                    </ReactMarkdown>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
