import { useState } from 'react';
import { QuizManagementTab } from '@/components/engagement/QuizManagementTab';
import { SurveyManagementTab } from '@/components/engagement/SurveyManagementTab';

type EngagementView = 'quizzes' | 'surveys';

export function EngagementManagementTab() {
  const [activeView, setActiveView] = useState<EngagementView>('quizzes');

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold">Engagement</h2>
            <p className="text-xs text-muted-foreground">
            </p>
          </div>

          <div className="flex items-center gap-2 rounded-full border border-border/60 bg-muted/60 p-1">
            <button
              type="button"
              onClick={() => setActiveView('quizzes')}
              className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                activeView === 'quizzes'
                  ? 'bg-emerald-100 text-emerald-800 shadow-sm dark:bg-emerald-900/40 dark:text-emerald-200'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Quizzes
            </button>

            <button
              type="button"
              onClick={() => setActiveView('surveys')}
              className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                activeView === 'surveys'
                  ? 'bg-emerald-100 text-emerald-800 shadow-sm dark:bg-emerald-900/40 dark:text-emerald-200'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Surveys
            </button>
          </div>
        </div>
      </section>

      {activeView === 'quizzes' ? <QuizManagementTab /> : <SurveyManagementTab />}
    </div>
  );
}

export default EngagementManagementTab;
