import ReactMarkdown from 'react-markdown';
import { useState } from 'react';
import { displayModelName } from '../councilUi';
import './Stage3.css';

function deAnonymizeText(text, labelToModel) {
  if (!labelToModel) return text;

  let result = text;
  Object.entries(labelToModel).forEach(([label, model]) => {
    result = result.replace(new RegExp(label, 'g'), `**${displayModelName(model)}**`);
  });
  return result;
}

export default function Stage3({
  finalResponse,
  stage1Responses = [],
  stage2Rankings = [],
  labelToModel,
}) {
  const [activeTab, setActiveTab] = useState('common');

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
            <div className="final-text markdown-content">
              <ReactMarkdown>{finalResponse.response}</ReactMarkdown>
            </div>
          </>
        )}

        {activeTab === 'answers' && (
          <div className="final-list">
            {stage1Responses.length === 0 ? (
              <p className="empty-tab">Ayrı cevap yok.</p>
            ) : (
              stage1Responses.map((response) => (
                <div key={response.model} className="final-list-item">
                  <div className="final-list-title">{displayModelName(response)}</div>
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
            {stage2Rankings.length === 0 ? (
              <p className="empty-tab">Değerlendirme yok.</p>
            ) : (
              stage2Rankings.map((ranking) => (
                <div key={ranking.model} className="final-list-item">
                  <div className="final-list-title">{displayModelName(ranking)}</div>
                  <div className="markdown-content">
                    <ReactMarkdown>
                      {deAnonymizeText(ranking.ranking, labelToModel)}
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
