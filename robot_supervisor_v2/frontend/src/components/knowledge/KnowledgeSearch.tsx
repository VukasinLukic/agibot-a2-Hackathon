import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import type { KnowledgeSearchResult } from '@/api/types';
import { Search, X } from 'lucide-react';
import styles from './Knowledge.module.css';

interface KnowledgeSearchProps {
  refreshTrigger?: number;
}

export function KnowledgeSearch({ refreshTrigger = 0 }: KnowledgeSearchProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<KnowledgeSearchResult[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setQuery('');
    setResults([]);
  }, [refreshTrigger]);

  const handleSearch = async () => {
    setLoading(true);
    try {
      const data = await api.searchKnowledge(query);
      setResults(data);
    } catch (error) {
      console.error('Error searching knowledge base:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Search</h3>
      </div>

      <div className={styles.row}>
        <input
          type="text"
          className={styles.input}
          placeholder="Search knowledge base..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          onClick={handleSearch}
          className={styles.btnPrimary}
          disabled={loading || !query}
        >
          <Search size={16} />
          {loading ? 'Searching...' : 'Search'}
        </button>
        <button
          onClick={() => {
            setQuery('');
            setResults([]);
          }}
          className={styles.btnSecondary}
          disabled={loading && !query && results.length === 0}
        >
          <X size={16} />
          Clear
        </button>
      </div>

      {loading && <div className={styles.muted}>Searching...</div>}
      {results.length > 0 && (
        <div className={styles.results}>
          {results.map((res, idx) => (
            <div key={idx} className={styles.resultCard}>
              <div className={styles.resultTitle}>{res.index_title ?? res.document_id}</div>
              <div className={styles.resultText}>{res.text}</div>
              <div className={styles.resultMeta}>
                {res.document_id} | Score: {res.score.toFixed(4)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
