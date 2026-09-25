import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type {
  ActiveSurveyResponse,
  SurveyExportFormat,
  SurveyExportStatus,
  SurveyItem,
  SurveyQuestionItem,
  SurveyResponseKind,
  SurveyRunItem,
} from '@/api/types';

type ConfirmDeleteAction = 'survey' | 'question' | 'run';
type SurveyDetailsTab = 'view' | 'create' | 'update';

type SurveyForm = {
  slug: string;
  title: string;
  description: string;
};

type QuestionForm = {
  question_key: string;
  question: string;
  response_kind: SurveyResponseKind;
  optionsText: string;
  allow_skip: boolean;
  order_index: number;
};

const EMPTY_SURVEY_FORM: SurveyForm = {
  slug: '',
  title: '',
  description: '',
};

const EMPTY_QUESTION_FORM: QuestionForm = {
  question_key: '',
  question: '',
  response_kind: 'single_choice',
  optionsText: '',
  allow_skip: true,
  order_index: 0,
};

const makeEmptyQuestionForm = (orderIndex: number): QuestionForm => ({
  ...EMPTY_QUESTION_FORM,
  order_index: orderIndex,
});

const slugify = (value: string) =>
  value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');

const parseOptions = (value: string) =>
  value
    .split('\n')
    .map((entry) => entry.trim())
    .filter(Boolean);

const truncateText = (value: string, maxLength: number, fallback: string) => {
  const text = value.trim();
  if (!text) {
    return fallback;
  }

  if (text.length <= maxLength) {
    return text;
  }

  return `${text.slice(0, maxLength - 3).trimEnd()}...`;
};

const formatCount = (count: number, singular: string, plural = `${singular}s`) =>
  `${count} ${count === 1 ? singular : plural}`;

const formatResponseKind = (kind: SurveyResponseKind) => {
  switch (kind) {
    case 'single_choice':
      return 'Single choice';
    case 'multiple_choice':
      return 'Multiple choice';
    case 'free_text':
      return 'Free text';
    case 'ranking':
      return 'Ranking';
    default:
      return kind;
  }
};

const formatDateTime = (value?: string | null) => {
  if (!value) {
    return 'Unknown time';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
};

export function SurveyManagementTab() {
  const [surveys, setSurveys] = useState<SurveyItem[]>([]);
  const [questions, setQuestions] = useState<SurveyQuestionItem[]>([]);
  const [selectedSurveyId, setSelectedSurveyId] = useState<number | string | null>(null);
  const [selectedQuestionId, setSelectedQuestionId] = useState<number | string | null>(null);
  const [expandedQuestionId, setExpandedQuestionId] = useState<number | string | null>(null);
  const [showNewSurveyForm, setShowNewSurveyForm] = useState(false);
  const [showSurveyDetails, setShowSurveyDetails] = useState(true);
  const [surveyDetailsTab, setSurveyDetailsTab] = useState<SurveyDetailsTab>('view');
  const [confirmDeleteAction, setConfirmDeleteAction] = useState<ConfirmDeleteAction | null>(null);
  const [activeSurvey, setActiveSurvey] = useState<ActiveSurveyResponse>({});
  const [surveyForm, setSurveyForm] = useState<SurveyForm>(EMPTY_SURVEY_FORM);
  const [createQuestionForm, setCreateQuestionForm] = useState<QuestionForm>(makeEmptyQuestionForm(0));
  const [editQuestionForm, setEditQuestionForm] = useState<QuestionForm>(makeEmptyQuestionForm(0));
  const [questionCountBySurvey, setQuestionCountBySurvey] = useState<Record<string, number>>({});
  const [exportStatus, setExportStatus] = useState<SurveyExportStatus>({ available: false });
  const [surveyRuns, setSurveyRuns] = useState<SurveyRunItem[]>([]);
  const [catalogExportLoading, setCatalogExportLoading] = useState(false);
  const [archiveDeleteLoading, setArchiveDeleteLoading] = useState(false);
  const [archiveExportLoading, setArchiveExportLoading] = useState<SurveyExportFormat | null>(null);
  const [runExportLoading, setRunExportLoading] = useState<string | null>(null);
  const [runDeleteLoadingId, setRunDeleteLoadingId] = useState<number | null>(null);
  const [pendingDeleteRun, setPendingDeleteRun] = useState<SurveyRunItem | null>(null);
  const [loading, setLoading] = useState(false);

  const selectedSurvey = surveys.find((survey) => String(survey.id) === String(selectedSurveyId) || survey.slug === selectedSurveyId) ?? null;
  const selectedQuestion = questions.find((question) => String(question.id) === String(selectedQuestionId) || question.question_key === selectedQuestionId) ?? null;
  const activeSurveyItem = surveys.find((survey) => survey.slug === activeSurvey.survey_slug) ?? null;
  const createNeedsOptions = createQuestionForm.response_kind !== 'free_text';
  const editNeedsOptions = editQuestionForm.response_kind !== 'free_text';
  const activeSurveyQuestionCount = activeSurveyItem ? questionCountBySurvey[String(activeSurveyItem.id)] ?? 0 : 0;

  const selectSurvey = (surveyId: number | string | null) => {
    setSelectedSurveyId(surveyId);
    setSelectedQuestionId(null);
    setExpandedQuestionId(null);
    setConfirmDeleteAction(null);
    setSurveyDetailsTab('view');
    setShowSurveyDetails(Boolean(surveyId));
  };

  const refreshSurveyExportStatus = async () => {
    try {
      const status = await api.getSurveyExportStatus();
      setExportStatus(status);
      if (status.archive?.archived) {
        await refreshSurveyRuns();
      }
    } catch (err) {
      setExportStatus({
        available: false,
        reason: err instanceof Error ? err.message : 'Survey export status is unavailable.',
      });
    }
  };

  const refreshSurveyRuns = async () => {
    try {
      const result = await api.listSurveyRuns();
      setSurveyRuns(result.items);
    } catch (err) {
      setSurveyRuns([]);
      toast.error(`Failed to load saved survey runs: ${err}`);
    }
  };

  const refreshLibrary = async (preferredSurveyId?: number | string | null) => {
    try {
      const [optionsResult, activeResult] = await Promise.allSettled([
        api.getSurveyOptions(),
        api.getActiveSurvey(),
      ]);

      let nextSurveys: SurveyItem[] = [];
      let nextCounts: Record<string, number> = {};

      if (optionsResult.status === 'fulfilled') {
        const optionItems = optionsResult.value.options?.surveys ?? [];
        nextSurveys = optionItems.map((survey) => ({
          id: survey.id,
          slug: survey.slug,
          title: survey.title,
          description: survey.description,
          created_at: null,
          updated_at: null,
        }));
        nextCounts = Object.fromEntries(optionItems.map((survey) => [String(survey.id), survey.question_count]));
      }

      if (nextSurveys.length === 0) {
        const surveyData = await api.listSurveys();
        nextSurveys = surveyData.items;
      }

      setSurveys(nextSurveys);
      setQuestionCountBySurvey(nextCounts);
      setActiveSurvey(activeResult.status === 'fulfilled' ? activeResult.value : {});

      if (preferredSurveyId !== undefined) {
        setSelectedSurveyId(preferredSurveyId);
        return;
      }

      if (selectedSurveyId && nextSurveys.some((item) => String(item.id) === String(selectedSurveyId) || item.slug === selectedSurveyId)) {
        return;
      }

      setSelectedSurveyId(nextSurveys[0]?.id ?? null);
    } catch (err) {
      setSurveys([]);
      setQuestionCountBySurvey({});
      setActiveSurvey({});
      toast.error(`Failed to load surveys: ${err}`);
    }
  };

  const refreshSelectedSurveyData = async (survey: SurveyItem | null) => {
    if (!survey) {
      setQuestions([]);
      setSelectedQuestionId(null);
      setExpandedQuestionId(null);
      return;
    }

    try {
      const questionData = await api.listSurveyQuestions(survey.slug);

      setQuestions(questionData.items);
      setSelectedQuestionId((current) => {
        if (current && questionData.items.some((item) => String(item.id) === String(current) || item.question_key === current)) {
          return current;
        }

        return questionData.items[0]?.id ?? null;
      });
      setExpandedQuestionId((current) => {
        if (current && questionData.items.some((item) => String(item.id) === String(current) || item.question_key === current)) {
          return current;
        }

        return null;
      });
      setQuestionCountBySurvey((prev) => ({
        ...prev,
        [String(survey.id)]: questionData.items.length,
      }));
    } catch (err) {
      setQuestions([]);
      toast.error(`Failed to load survey details: ${err}`);
    }
  };

  useEffect(() => {
    void refreshLibrary();
    void refreshSurveyExportStatus();
    void refreshSurveyRuns();

    const intervalId = window.setInterval(() => {
      void refreshSurveyExportStatus();
    }, 5000);

    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    if (!selectedSurvey) {
      void refreshSelectedSurveyData(null);
      return;
    }

    void refreshSelectedSurveyData(selectedSurvey);
  }, [selectedSurveyId, surveys]);

  useEffect(() => {
    setCreateQuestionForm(makeEmptyQuestionForm(questions.length));
  }, [selectedSurveyId, questions.length]);

  useEffect(() => {
    if (!selectedQuestion) {
      setEditQuestionForm(makeEmptyQuestionForm(questions.length));
      return;
    }

    setEditQuestionForm({
      question_key: selectedQuestion.question_key,
      question: selectedQuestion.question,
      response_kind: selectedQuestion.response_kind,
      optionsText: selectedQuestion.options.join('\n'),
      allow_skip: selectedQuestion.allow_skip,
      order_index: selectedQuestion.order_index,
    });
  }, [selectedQuestionId, questions]);

  const updateSurveyForm = (key: keyof SurveyForm, value: string) => {
    setSurveyForm((prev) => ({ ...prev, [key]: value }));
  };

  const updateCreateQuestionForm = (key: keyof QuestionForm, value: string | number | boolean) => {
    setCreateQuestionForm((prev) => ({ ...prev, [key]: value }));
  };

  const updateEditQuestionForm = (key: keyof QuestionForm, value: string | number | boolean) => {
    setEditQuestionForm((prev) => ({ ...prev, [key]: value }));
  };

  const beginNewSurvey = () => {
    setSurveyForm(EMPTY_SURVEY_FORM);
    setShowNewSurveyForm(true);
  };

  const toggleQuestionDetails = (questionId: number | string) => {
    setSelectedQuestionId(questionId);
    setExpandedQuestionId((current) => (current === questionId ? null : questionId));
  };

  const openQuestionUpdate = (questionId: number | string) => {
    setConfirmDeleteAction(null);
    setSelectedQuestionId(questionId);
    setExpandedQuestionId(questionId);
    setSurveyDetailsTab('update');
  };

  const handleCreateSurvey = async () => {
    const generatedSlug = slugify(surveyForm.title);
    const payload = {
      slug: generatedSlug,
      title: surveyForm.title.trim(),
      description: surveyForm.description.trim(),
    };

    if (!payload.title || !payload.slug) {
      toast.error('Survey title is required.');
      return;
    }

    setLoading(true);
    try {
      const result = await api.createSurvey(payload);
      setSurveyForm(EMPTY_SURVEY_FORM);
      setShowNewSurveyForm(false);
      await refreshLibrary(result.item.id);
      setConfirmDeleteAction(null);
      setExpandedQuestionId(null);
      setSurveyDetailsTab('create');
      setShowSurveyDetails(true);
      toast.success('Survey created.');
    } catch (err) {
      toast.error(`Failed to create survey: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteSurvey = async () => {
    if (!selectedSurvey) {
      return;
    }

    setLoading(true);
    try {
      await api.deleteSurvey(selectedSurvey.slug);
      setConfirmDeleteAction(null);
      await refreshLibrary(null);
      setSelectedQuestionId(null);
      setExpandedQuestionId(null);
      setShowSurveyDetails(false);
      setQuestions([]);
      toast.success('Survey deleted.');
    } catch (err) {
      toast.error(`Failed to delete survey: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleSetActiveSurvey = async () => {
    if (!selectedSurvey) {
      return;
    }

    setLoading(true);
    try {
      const result = await api.updateSurveyConfig(selectedSurvey.slug);
      setActiveSurvey(result.active);
      toast.success(`Active survey set to ${result.active.survey_title}`);
    } catch (err) {
      toast.error(`Failed to activate survey: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleExportSurveyCatalog = async () => {
    setCatalogExportLoading(true);
    try {
      const { blob, filename } = await api.downloadSurveyCatalogExport();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('Survey catalog exported.');
    } catch (err) {
      toast.error(`Failed to export survey catalog: ${err}`);
    } finally {
      setCatalogExportLoading(false);
    }
  };

  const handleDeleteArchive = async () => {
    setArchiveDeleteLoading(true);
    try {
      const result = await api.deleteSurveyRuns();
      await api.deleteLatestSurveyCache();
      await refreshSurveyRuns();
      await refreshSurveyExportStatus();
      toast.success(
        result.deleted > 0
          ? `Deleted ${result.deleted} saved survey runs and cleared the latest cache.`
          : 'Cleared the latest cache. No saved survey runs were present.',
      );
    } catch (err) {
      toast.error(`Failed to delete saved survey runs: ${err}`);
    } finally {
      setArchiveDeleteLoading(false);
    }
  };

  const handleExportSurveyArchive = async (format: SurveyExportFormat) => {
    setArchiveExportLoading(format);
    try {
      const { blob, filename } = await api.downloadSurveyRunsExport(format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('Survey archive exported.');
    } catch (err) {
      toast.error(`Failed to export survey archive: ${err}`);
    } finally {
      setArchiveExportLoading(null);
    }
  };

  const handleExportSurveyRun = async (run: SurveyRunItem, format: SurveyExportFormat) => {
    setRunExportLoading(`${run.id}:${format}`);
    try {
      const { blob, filename } = await api.downloadSurveyRunExport(run.id, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('Saved survey exported.');
    } catch (err) {
      toast.error(`Failed to export saved survey: ${err}`);
    } finally {
      setRunExportLoading(null);
    }
  };

  const handleCreateQuestion = async () => {
    if (!selectedSurvey) {
      return;
    }

    const options = createNeedsOptions ? parseOptions(createQuestionForm.optionsText) : [];
    const generatedQuestionKey = slugify(createQuestionForm.question);
    const payload = {
      question_key: generatedQuestionKey,
      question: createQuestionForm.question.trim(),
      response_kind: createQuestionForm.response_kind,
      options,
      allow_skip: createQuestionForm.allow_skip,
      order_index: Number(createQuestionForm.order_index) || 0,
    };

    if (!payload.question || !payload.question_key) {
      toast.error('Question text is required.');
      return;
    }

    if (createNeedsOptions && options.length < 2) {
      toast.error('At least 2 options are required for this response type.');
      return;
    }

    setLoading(true);
    try {
      const result = await api.createSurveyQuestion(selectedSurvey.slug, payload);
      await refreshSelectedSurveyData(selectedSurvey);
      setSelectedQuestionId(result.item.id);
      setCreateQuestionForm(makeEmptyQuestionForm(questions.length + 1));
      toast.success('Question created.');
    } catch (err) {
      toast.error(`Failed to create question: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateQuestion = async () => {
    if (!selectedQuestion || !selectedSurvey) {
      return;
    }

    const options = editNeedsOptions ? parseOptions(editQuestionForm.optionsText) : [];
    const payload = {
      question_key: selectedQuestion.question_key,
      question: editQuestionForm.question.trim(),
      response_kind: editQuestionForm.response_kind,
      options,
      allow_skip: editQuestionForm.allow_skip,
      order_index: Number(editQuestionForm.order_index) || 0,
    };

    if (!payload.question || !payload.question_key) {
      toast.error('Question text is required.');
      return;
    }

    if (editNeedsOptions && options.length < 2) {
      toast.error('At least 2 options are required for this response type.');
      return;
    }

    setLoading(true);
    try {
      await api.updateSurveyQuestion(selectedQuestion.question_key, payload);
      await refreshSelectedSurveyData(selectedSurvey);
      toast.success('Question updated.');
    } catch (err) {
      toast.error(`Failed to update question: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteQuestion = async () => {
    if (!selectedQuestion || !selectedSurvey) {
      return;
    }

    setLoading(true);
    try {
      await api.deleteSurveyQuestion(selectedQuestion.question_key);
      await refreshSelectedSurveyData(selectedSurvey);
      setConfirmDeleteAction(null);
      toast.success('Question deleted.');
    } catch (err) {
      toast.error(`Failed to delete question: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteSurveyRun = async () => {
    if (!pendingDeleteRun) {
      return;
    }

    setRunDeleteLoadingId(pendingDeleteRun.id);
    try {
      await api.deleteSurveyRun(pendingDeleteRun.id);
      await refreshSurveyRuns();
      setPendingDeleteRun(null);
      setConfirmDeleteAction(null);
      toast.success('Saved survey run deleted.');
    } catch (err) {
      toast.error(`Failed to delete saved survey run: ${err}`);
    } finally {
      setRunDeleteLoadingId(null);
    }
  };

  const handleConfirmDelete = async () => {
    if (confirmDeleteAction === 'survey') {
      await handleDeleteSurvey();
      return;
    }

    if (confirmDeleteAction === 'question') {
      await handleDeleteQuestion();
    }

    if (confirmDeleteAction === 'run') {
      await handleDeleteSurveyRun();
    }
  };

  const renderSurveyDetailsPanel = () => {
    if (!selectedSurvey) {
      return null;
    }

    return (
      <section className="mt-6 rounded-2xl border border-border/50 bg-background/95 shadow-sm">
        <div
          className={`flex flex-wrap items-start justify-between gap-3 bg-muted/30 px-5 py-4 ${
            showSurveyDetails ? 'border-b border-border/50' : ''
          }`}
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-semibold">{selectedSurvey.title}</h3>
              {activeSurvey.survey_slug === selectedSurvey.slug ? (
                <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200">
                  ACTIVE
                </span>
              ) : null}
            </div>
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
              {truncateText(selectedSurvey.description, 220, 'No description yet.')}
            </p>
            <div className="mt-3 flex flex-wrap gap-2 text-[11px] font-medium text-muted-foreground">
              <span className="rounded-full border border-border/60 bg-muted px-2.5 py-1">
                {formatCount(questions.length, 'question')}
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowSurveyDetails((current) => !current)}
              className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
            >
              {showSurveyDetails ? 'Hide Details' : 'Show Details'}
            </button>
          </div>
        </div>

        {showSurveyDetails ? (
          <div className="bg-background/80 p-5">
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setConfirmDeleteAction(null);
                    setSurveyDetailsTab('view');
                  }}
                  className={`rounded-full px-4 py-2 text-sm font-medium ${
                    surveyDetailsTab === 'view'
                      ? 'bg-blue-600 text-white'
                      : 'border border-border bg-background text-foreground'
                  }`}
                >
                  Questions
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setConfirmDeleteAction(null);
                    setSurveyDetailsTab('create');
                  }}
                  className={`rounded-full px-4 py-2 text-sm font-medium ${
                    surveyDetailsTab === 'create'
                      ? 'bg-blue-600 text-white'
                      : 'border border-border bg-background text-foreground'
                  }`}
                >
                  Create
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setConfirmDeleteAction(null);
                    setSurveyDetailsTab('update');
                  }}
                  disabled={questions.length === 0}
                  className={`rounded-full px-4 py-2 text-sm font-medium ${
                    surveyDetailsTab === 'update'
                      ? 'bg-blue-600 text-white'
                      : 'border border-border bg-background text-foreground'
                  } disabled:opacity-50`}
                >
                  Update
                </button>
              </div>

              {surveyDetailsTab === 'view' ? (
                <section className="rounded-xl border border-border/60 bg-background/90 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-sm font-semibold">Questions</h4>
                  </div>

                  {questions.length > 0 ? (
                    <div className="mt-4 max-h-[28rem] space-y-3 overflow-y-auto pr-1">
                      {questions.map((question, index) => {
                        const isExpanded = String(expandedQuestionId) === String(question.id);
                        const isEditing = String(selectedQuestionId) === String(question.id);

                        return (
                          <div
                            key={question.id}
                            onClick={() => setSelectedQuestionId(question.id)}
                            className={`w-full cursor-pointer rounded-xl border px-4 py-3 text-left transition ${
                              isExpanded || isEditing
                                ? 'border-blue-400 bg-blue-50 shadow-sm dark:bg-blue-950/30'
                                : 'border-border/60 bg-background hover:border-blue-300 hover:bg-background/90'
                            }`}
                          >
                            <div className="flex flex-wrap items-start justify-between gap-3">
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium text-muted-foreground">
                                  <span className="rounded-full border border-border/60 bg-muted px-2.5 py-1">
                                    Question {index + 1}
                                  </span>
                                  <span className="rounded-full bg-sky-100 px-2.5 py-1 text-sky-700 dark:bg-sky-900/40 dark:text-sky-200">
                                    {formatResponseKind(question.response_kind)}
                                  </span>
                                  <span className="rounded-full border border-border/60 bg-muted px-2.5 py-1">
                                    {question.allow_skip ? 'Skippable' : 'Answer required'}
                                  </span>
                                </div>
                                <div className="mt-3 text-sm font-medium leading-6">
                                  {truncateText(question.question, 160, 'Untitled question')}
                                </div>
                              </div>

                              <div className="flex shrink-0 flex-wrap items-center gap-2">
                                <button
                                  type="button"
                                  onClick={() => toggleQuestionDetails(question.id)}
                                  className="rounded-full border border-border bg-background px-3 py-1.5 text-xs font-medium"
                                >
                                  {isExpanded ? 'Hide Details' : 'View Details'}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => openQuestionUpdate(question.id)}
                                  className={`rounded-full px-3 py-1.5 text-xs font-medium ${
                                    isEditing
                                      ? 'bg-blue-600 text-white'
                                      : 'border border-border bg-background text-foreground'
                                  }`}
                                >
                                  Update Question
                                </button>
                              </div>
                            </div>

                            {isExpanded ? (
                              <div className="mt-4 grid gap-4 border-t border-border/60 pt-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(16rem,0.8fr)]">
                                <div className="space-y-4">
                                  <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
                                    <span className="text-xs font-medium text-muted-foreground">Question</span>
                                    <p className="mt-2 text-sm leading-6 text-foreground">{question.question}</p>
                                  </div>

                                  {question.options.length > 0 ? (
                                    <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
                                      <span className="text-xs font-medium text-muted-foreground">Options</span>
                                      <div className="mt-3 flex flex-wrap gap-2">
                                        {question.options.map((option) => (
                                          <span
                                            key={option}
                                            className="rounded-full bg-background px-2.5 py-1 text-xs text-muted-foreground"
                                          >
                                            {option}
                                          </span>
                                        ))}
                                      </div>
                                    </div>
                                  ) : (
                                    <div className="rounded-xl border border-dashed border-border/60 bg-muted/20 px-3 py-3 text-sm text-muted-foreground">
                                      Free-text question with no predefined options.
                                    </div>
                                  )}
                                </div>

                                <div className="space-y-3">
                                  <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
                                    <span className="text-xs font-medium text-muted-foreground">Response Type</span>
                                    <p className="mt-2 text-sm text-foreground">
                                      {formatResponseKind(question.response_kind)}
                                    </p>
                                  </div>

                                  <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
                                    <span className="text-xs font-medium text-muted-foreground">Skip Behavior</span>
                                    <p className="mt-2 text-sm text-foreground">
                                      {question.allow_skip ? 'This question can be skipped.' : 'An answer is required.'}
                                    </p>
                                  </div>
                                </div>
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="mt-4 rounded-xl border border-dashed border-border/60 bg-background p-4 text-sm text-muted-foreground">
                      This survey has no questions yet.
                    </div>
                  )}
                </section>
              ) : null}

              {surveyDetailsTab === 'create' ? (
                <section className="rounded-xl border border-border/60 bg-background/90 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-sm font-semibold">Create Question</h4>
                  </div>

                  <div className="mt-4 grid gap-4">
                    <label className="flex flex-col gap-1 text-sm">
                      <span>Question</span>
                      <textarea
                        value={createQuestionForm.question}
                        onChange={(e) => updateCreateQuestionForm('question', e.target.value)}
                        placeholder="Write the question here..."
                        className="min-h-[72px] rounded-lg border border-border bg-background px-3 py-2"
                      />
                    </label>

                    <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
                      <label className="flex flex-col gap-1 text-sm">
                        <span>Response kind</span>
                        <select
                          value={createQuestionForm.response_kind}
                          onChange={(e) => updateCreateQuestionForm('response_kind', e.target.value as SurveyResponseKind)}
                          className="rounded-lg border border-border bg-background px-3 py-2"
                        >
                          <option value="single_choice">Single choice</option>
                          <option value="multiple_choice">Multiple choice</option>
                          <option value="free_text">Free text</option>
                          <option value="ranking">Ranking</option>
                        </select>
                      </label>

                      <label className="inline-flex w-fit items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm">
                        <input
                          type="checkbox"
                          checked={createQuestionForm.allow_skip}
                          className="h-4 w-4 rounded border-border"
                          onChange={(e) => updateCreateQuestionForm('allow_skip', e.target.checked)}
                        />
                        <span>Allow skip</span>
                      </label>
                    </div>

                    {createNeedsOptions ? (
                      <label className="flex flex-col gap-1 text-sm">
                        <span>Options, one per line</span>
                        <textarea
                          value={createQuestionForm.optionsText}
                          onChange={(e) => updateCreateQuestionForm('optionsText', e.target.value)}
                          placeholder={'Option 1\nOption 2\nOption 3'}
                          className="min-h-[96px] rounded-lg border border-border bg-background px-3 py-2"
                        />
                      </label>
                    ) : (
                      <div className="rounded-xl border border-dashed border-border/60 bg-background p-4 text-sm text-muted-foreground">
                        This response type does not need predefined options.
                      </div>
                    )}
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void handleCreateQuestion()}
                      disabled={loading || !selectedSurvey}
                      className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                    >
                      Create Question
                    </button>
                  </div>
                </section>
              ) : null}

              {surveyDetailsTab === 'update' ? (
                <section className="rounded-xl border border-border/60 bg-background/90 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-sm font-semibold">Update Question</h4>
                  </div>

                  {selectedQuestion ? (
                    <>
                      <div className="mt-4 grid gap-4">
                        <label className="flex flex-col gap-1 text-sm">
                          <span>Question</span>
                          <textarea
                            value={editQuestionForm.question}
                            onChange={(e) => updateEditQuestionForm('question', e.target.value)}
                            placeholder="Write the question here..."
                            className="min-h-[72px] rounded-lg border border-border bg-background px-3 py-2"
                          />
                        </label>

                        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
                          <label className="flex flex-col gap-1 text-sm">
                            <span>Response kind</span>
                            <select
                              value={editQuestionForm.response_kind}
                              onChange={(e) => updateEditQuestionForm('response_kind', e.target.value as SurveyResponseKind)}
                              className="rounded-lg border border-border bg-background px-3 py-2"
                            >
                              <option value="single_choice">Single choice</option>
                              <option value="multiple_choice">Multiple choice</option>
                              <option value="free_text">Free text</option>
                              <option value="ranking">Ranking</option>
                            </select>
                          </label>

                          <label className="inline-flex w-fit items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm">
                            <input
                              type="checkbox"
                              checked={editQuestionForm.allow_skip}
                              className="h-4 w-4 rounded border-border"
                              onChange={(e) => updateEditQuestionForm('allow_skip', e.target.checked)}
                            />
                            <span>Allow skip</span>
                          </label>
                        </div>

                        {editNeedsOptions ? (
                          <label className="flex flex-col gap-1 text-sm">
                            <span>Options, one per line</span>
                            <textarea
                              value={editQuestionForm.optionsText}
                              onChange={(e) => updateEditQuestionForm('optionsText', e.target.value)}
                              placeholder={'Option 1\nOption 2\nOption 3'}
                              className="min-h-[96px] rounded-lg border border-border bg-background px-3 py-2"
                            />
                          </label>
                        ) : (
                          <div className="rounded-xl border border-dashed border-border/60 bg-background p-4 text-sm text-muted-foreground">
                            This response type does not need predefined options.
                          </div>
                        )}
                      </div>

                      <div className="mt-4 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => void handleUpdateQuestion()}
                          disabled={loading || !selectedQuestion}
                          className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                        >
                          Save Question
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmDeleteAction('question')}
                          disabled={loading || !selectedQuestion}
                          className="rounded-full bg-red-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                        >
                          Delete Question
                        </button>
                      </div>

                      {confirmDeleteAction === 'question' ? (
                        <div className="mt-3 rounded-xl border border-red-200/70 bg-background px-4 py-4 shadow-sm dark:border-red-900/50">
                          <h4 className="text-sm font-semibold text-red-700 dark:text-red-300">Delete this question?</h4>
                          <p className="mt-1 text-sm text-muted-foreground">
                            Delete "{selectedQuestion?.question ?? 'this question'}"?
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">This action cannot be undone.</p>

                          <div className="mt-4 flex flex-wrap justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteAction(null)}
                              className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
                            >
                              Cancel
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleConfirmDelete()}
                              disabled={loading}
                              className="rounded-full bg-red-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                            >
                              Yes, Delete
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <div className="mt-4 rounded-xl border border-dashed border-border/60 bg-background p-4 text-sm text-muted-foreground">
                      Select a question from the Questions tab, then choose Update Question.
                    </div>
                  )}
                </section>
              ) : null}
            </div>
          </div>
        ) : null}
      </section>
    );
  };

  return (
    <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">Survey Configuration</h2>
        </div>

        <div className="min-w-[15rem] rounded-xl border border-emerald-200/70 bg-emerald-50/60 px-4 py-3 text-right dark:border-emerald-900/50 dark:bg-emerald-950/20">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">
            Active Survey
          </div>
          {activeSurvey.survey_title ? (
            <>
              <div className="mt-1 text-sm font-semibold">{activeSurvey.survey_title}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                {formatCount(activeSurveyQuestionCount, 'question')}
              </div>
            </>
          ) : (
            <div className="mt-1 text-sm text-muted-foreground">No active survey selected.</div>
          )}
        </div>
      </div>

      <div className="space-y-6">
        <section>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-semibold">Available Surveys</h3>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handleExportSurveyCatalog()}
                disabled={catalogExportLoading || surveys.length === 0}
                className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium disabled:opacity-50"
              >
                {catalogExportLoading ? 'Exporting...' : 'Export Excel'}
              </button>
              <button
                type="button"
                onClick={beginNewSurvey}
                className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
              >
                Add Survey
              </button>
            </div>
          </div>

          <div className="mt-4">
            {surveys.length > 0 ? (
              <div className="space-y-4">
                <div className="space-y-3">
                  <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
                    <label className="flex flex-col gap-1 text-sm">
                      <span>Select survey</span>
                      <select
                        value={selectedSurvey?.slug ?? ''}
                        onChange={(e) => selectSurvey(e.target.value || null)}
                        className="rounded-lg border border-border bg-background px-3 py-2"
                      >
                        <option value="">Choose a survey...</option>
                        {surveys.map((survey) => (
                          <option key={survey.slug} value={survey.slug}>
                            {survey.title}
                          </option>
                        ))}
                      </select>
                    </label>

                    <button
                      type="button"
                      onClick={() => void handleSetActiveSurvey()}
                      disabled={loading || !selectedSurvey || activeSurvey.survey_slug === selectedSurvey.slug}
                      className="self-end rounded-full bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                    >
                      Set Active
                    </button>

                    <button
                      type="button"
                      onClick={() => setConfirmDeleteAction('survey')}
                      disabled={loading || !selectedSurvey}
                      className="self-end rounded-full bg-red-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                    >
                      Delete Survey
                    </button>
                  </div>

                  {confirmDeleteAction === 'survey' && selectedSurvey ? (
                    <div className="rounded-xl border border-red-200/70 bg-background px-4 py-4 shadow-sm dark:border-red-900/50">
                      <h4 className="text-sm font-semibold text-red-700 dark:text-red-300">Delete this survey?</h4>
                      <p className="mt-1 text-sm text-muted-foreground">Delete "{selectedSurvey.title}"?</p>
                      <p className="mt-1 text-xs text-muted-foreground">This action cannot be undone.</p>

                      <div className="mt-4 flex flex-wrap justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setConfirmDeleteAction(null)}
                          className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleConfirmDelete()}
                          disabled={loading}
                          className="rounded-full bg-red-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                        >
                          Yes, Delete
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>

              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-border/60 bg-background p-4 text-sm text-muted-foreground">
                No surveys found in the local catalog yet.
              </div>
            )}
          </div>
        </section>

        <section className="rounded-xl border border-border/60 bg-background/90 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold">Completed Survey Runs</h3>
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handleExportSurveyArchive('xlsx')}
                disabled={archiveExportLoading !== null || surveyRuns.length === 0}
                className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium disabled:opacity-50"
              >
                {archiveExportLoading === 'xlsx' ? 'Exporting...' : `Export All Excel (${surveyRuns.length})`}
              </button>
              <button
                type="button"
                onClick={() => void handleExportSurveyArchive('pdf')}
                disabled={archiveExportLoading !== null || surveyRuns.length === 0}
                className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium disabled:opacity-50"
              >
                {archiveExportLoading === 'pdf' ? 'Exporting...' : `Export All PDF (${surveyRuns.length})`}
              </button>
              <button
                type="button"
                onClick={() => void handleExportSurveyArchive('word')}
                disabled={archiveExportLoading !== null || surveyRuns.length === 0}
                className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium disabled:opacity-50"
              >
                {archiveExportLoading === 'word' ? 'Exporting...' : `Export All Word (${surveyRuns.length})`}
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteArchive()}
                disabled={archiveDeleteLoading || (surveyRuns.length === 0 && !exportStatus.available)}
                className="rounded-full bg-red-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {archiveDeleteLoading ? 'Deleting...' : 'Delete Cached'}
              </button>
            </div>
          </div>

          {surveyRuns.length > 0 ? (
            <div className="mt-4 max-h-[22rem] space-y-3 overflow-y-auto pr-1">
              {surveyRuns.map((run) => (
                <div
                  key={run.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/60 bg-muted/20 px-4 py-3"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="text-sm font-semibold">
                        {run.survey_title || run.survey_slug}
                      </h4>
                      <span className="rounded-full border border-border/60 bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                        {formatCount(run.responses_recorded, 'response')}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {formatDateTime(run.started_at ?? run.created_at)}
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void handleExportSurveyRun(run, 'xlsx')}
                      disabled={runExportLoading === `${run.id}:xlsx`}
                      className="rounded-full border border-border bg-background px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                    >
                      {runExportLoading === `${run.id}:xlsx` ? 'Exporting...' : 'Excel'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleExportSurveyRun(run, 'pdf')}
                      disabled={runExportLoading === `${run.id}:pdf`}
                      className="rounded-full border border-border bg-background px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                    >
                      {runExportLoading === `${run.id}:pdf` ? 'Exporting...' : 'PDF'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleExportSurveyRun(run, 'word')}
                      disabled={runExportLoading === `${run.id}:word`}
                      className="rounded-full border border-border bg-background px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                    >
                      {runExportLoading === `${run.id}:word` ? 'Exporting...' : 'Word'}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setPendingDeleteRun(run);
                        setConfirmDeleteAction('run');
                      }}
                      disabled={runDeleteLoadingId === run.id}
                      className="rounded-full bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                    >
                      {runDeleteLoadingId === run.id ? 'Deleting...' : 'Delete'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-4 rounded-xl border border-dashed border-border/60 bg-background p-4 text-sm text-muted-foreground">
              No completed survey runs saved yet.
            </div>
          )}

          {confirmDeleteAction === 'run' && pendingDeleteRun ? (
            <div className="mt-4 rounded-xl border border-red-200/70 bg-background px-4 py-4 shadow-sm dark:border-red-900/50">
              <h4 className="text-sm font-semibold text-red-700 dark:text-red-300">Delete this saved run?</h4>
              <p className="mt-1 text-sm text-muted-foreground">
                Delete "{pendingDeleteRun.survey_title || pendingDeleteRun.survey_slug}" from{' '}
                {formatDateTime(pendingDeleteRun.created_at)}?
              </p>

              <div className="mt-4 flex flex-wrap justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setPendingDeleteRun(null);
                    setConfirmDeleteAction(null);
                  }}
                  className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void handleConfirmDelete()}
                  disabled={runDeleteLoadingId === pendingDeleteRun.id}
                  className="rounded-full bg-red-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {runDeleteLoadingId === pendingDeleteRun.id ? 'Deleting...' : 'Yes, Delete'}
                </button>
              </div>
            </div>
          ) : null}
        </section>
      </div>

      {showNewSurveyForm ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-2xl overflow-hidden rounded-2xl border border-slate-200 shadow-2xl dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 bg-slate-50 px-5 py-4 dark:border-zinc-700 dark:bg-zinc-900">
              <div className="min-w-0">
                <h3 className="text-lg font-semibold">Add Survey</h3>
              </div>

              <button
                type="button"
                onClick={() => setShowNewSurveyForm(false)}
                className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
              >
                Close
              </button>
            </div>

            <div className="bg-slate-50 p-5 dark:bg-zinc-900">
              <div className="grid gap-4">
                <label className="flex flex-col gap-1 text-sm">
                  <span>Title</span>
                  <input
                    value={surveyForm.title}
                    onChange={(e) => updateSurveyForm('title', e.target.value)}
                    className="rounded-lg border border-border bg-background px-3 py-2"
                  />
                </label>

                <label className="flex flex-col gap-1 text-sm">
                  <span>Description</span>
                  <textarea
                    value={surveyForm.description}
                    onChange={(e) => updateSurveyForm('description', e.target.value)}
                    className="min-h-[100px] rounded-lg border border-border bg-background px-3 py-2"
                  />
                </label>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void handleCreateSurvey()}
                  disabled={loading}
                  className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
                >
                  Create Survey
                </button>
                <button
                  type="button"
                  onClick={beginNewSurvey}
                  className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
                >
                  Reset
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {renderSurveyDetailsPanel()}
    </section>
  );
}

export default SurveyManagementTab;
