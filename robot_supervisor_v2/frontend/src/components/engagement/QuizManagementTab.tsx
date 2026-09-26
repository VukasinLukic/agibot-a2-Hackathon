import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type { ActiveQuizResponse, QuizItem, QuizQuestionItem } from '@/api/types';

type ConfirmDeleteAction = 'quiz' | 'question';
type QuizDetailsTab = 'view' | 'create' | 'update';

type QuizForm = {
  slug: string;
  title: string;
  description: string;
  max_attempts: number;
};

type QuestionForm = {
  question_key: string;
  question: string;
  optionsText: string;
  correct_answer: string;
  hint: string;
  explanation: string;
  order_index: number;
};

const EMPTY_QUIZ_FORM: QuizForm = {
  slug: '',
  title: '',
  description: '',
  max_attempts: 2,
};

const EMPTY_QUESTION_FORM: QuestionForm = {
  question_key: '',
  question: '',
  optionsText: '',
  correct_answer: '',
  hint: '',
  explanation: '',
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

export function QuizManagementTab() {
  const [quizzes, setQuizzes] = useState<QuizItem[]>([]);
  const [questions, setQuestions] = useState<QuizQuestionItem[]>([]);
  const [selectedQuizId, setSelectedQuizId] = useState<number | string | null>(null);
  const [selectedQuestionId, setSelectedQuestionId] = useState<number | string | null>(null);
  const [expandedQuestionId, setExpandedQuestionId] = useState<number | string | null>(null);
  const [showNewQuizForm, setShowNewQuizForm] = useState(false);
  const [showQuizDetails, setShowQuizDetails] = useState(true);
  const [quizDetailsTab, setQuizDetailsTab] = useState<QuizDetailsTab>('view');
  const [confirmDeleteAction, setConfirmDeleteAction] = useState<ConfirmDeleteAction | null>(null);
  const [activeQuiz, setActiveQuiz] = useState<ActiveQuizResponse>({});
  const [quizForm, setQuizForm] = useState<QuizForm>(EMPTY_QUIZ_FORM);
  const [createQuestionForm, setCreateQuestionForm] = useState<QuestionForm>(makeEmptyQuestionForm(0));
  const [editQuestionForm, setEditQuestionForm] = useState<QuestionForm>(makeEmptyQuestionForm(0));
  const [questionCountByQuiz, setQuestionCountByQuiz] = useState<Record<string, number>>({});
  const [catalogExportLoading, setCatalogExportLoading] = useState(false);
  const [loading, setLoading] = useState(false);

  const selectedQuiz = quizzes.find((quiz) => String(quiz.id) === String(selectedQuizId) || quiz.slug === selectedQuizId) ?? null;
  const selectedQuestion = questions.find((question) => String(question.id) === String(selectedQuestionId) || question.question_key === selectedQuestionId) ?? null;
  const activeQuizItem = quizzes.find((quiz) => quiz.slug === activeQuiz.quiz_slug) ?? null;
  const parsedCreateOptions = parseOptions(createQuestionForm.optionsText);
  const parsedEditOptions = parseOptions(editQuestionForm.optionsText);
  const activeQuizQuestionCount = activeQuizItem ? questionCountByQuiz[String(activeQuizItem.id)] ?? 0 : 0;

  const selectQuiz = (quizId: number | string | null) => {
    setSelectedQuizId(quizId);
    setSelectedQuestionId(null);
    setExpandedQuestionId(null);
    setConfirmDeleteAction(null);
    setQuizDetailsTab('view');
    setShowQuizDetails(Boolean(quizId));
  };

  const refreshLibrary = async (preferredQuizId?: number | string | null) => {
    try {
      const [optionsResult, activeResult] = await Promise.allSettled([
        api.getQuizOptions(),
        api.getActiveQuiz(),
      ]);

      let nextQuizzes: QuizItem[] = [];
      let nextCounts: Record<string, number> = {};

      if (optionsResult.status === 'fulfilled') {
        const optionItems = optionsResult.value.options?.quizzes ?? [];
        nextQuizzes = optionItems.map((quiz) => ({
          id: quiz.id,
          slug: quiz.slug,
          title: quiz.title,
          description: quiz.description,
          max_attempts: quiz.max_attempts,
          created_at: null,
          updated_at: null,
        }));
        nextCounts = Object.fromEntries(optionItems.map((quiz) => [String(quiz.id), quiz.question_count]));
      }

      if (nextQuizzes.length === 0) {
        const quizData = await api.listQuizzes();
        nextQuizzes = quizData.items;
      }

      setQuizzes(nextQuizzes);
      setQuestionCountByQuiz(nextCounts);
      setActiveQuiz(activeResult.status === 'fulfilled' ? activeResult.value : {});

      if (preferredQuizId !== undefined) {
        setSelectedQuizId(preferredQuizId);
        return;
      }

      if (selectedQuizId && nextQuizzes.some((item) => String(item.id) === String(selectedQuizId) || item.slug === selectedQuizId)) {
        return;
      }

      setSelectedQuizId(nextQuizzes[0]?.id ?? null);
    } catch (err) {
      setQuizzes([]);
      setQuestionCountByQuiz({});
      setActiveQuiz({});
      toast.error(`Failed to load quizzes: ${err}`);
    }
  };

  const refreshSelectedQuizData = async (quiz: QuizItem | null) => {
    if (!quiz) {
      setQuestions([]);
      setSelectedQuestionId(null);
      setExpandedQuestionId(null);
      return;
    }

    try {
      const questionData = await api.listQuizQuestions(quiz.slug);

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
      setQuestionCountByQuiz((prev) => ({
        ...prev,
        [String(quiz.id)]: questionData.items.length,
      }));
    } catch (err) {
      setQuestions([]);
      toast.error(`Failed to load quiz details: ${err}`);
    }
  };

  useEffect(() => {
    void refreshLibrary();
  }, []);

  useEffect(() => {
    if (!selectedQuiz) {
      void refreshSelectedQuizData(null);
      return;
    }

    void refreshSelectedQuizData(selectedQuiz);
  }, [selectedQuizId, quizzes]);

  useEffect(() => {
    setCreateQuestionForm(makeEmptyQuestionForm(questions.length));
  }, [selectedQuizId, questions.length]);

  useEffect(() => {
    if (!selectedQuestion) {
      setEditQuestionForm(makeEmptyQuestionForm(questions.length));
      return;
    }

    setEditQuestionForm({
      question_key: selectedQuestion.question_key,
      question: selectedQuestion.question,
      optionsText: selectedQuestion.options.join('\n'),
      correct_answer: selectedQuestion.correct_answer,
      hint: selectedQuestion.hint,
      explanation: selectedQuestion.explanation,
      order_index: selectedQuestion.order_index,
    });
  }, [selectedQuestionId, questions]);

  const updateQuizForm = (key: keyof QuizForm, value: string | number) => {
    setQuizForm((prev) => ({ ...prev, [key]: value }));
  };

  const updateCreateQuestionForm = (key: keyof QuestionForm, value: string | number) => {
    setCreateQuestionForm((prev) => ({ ...prev, [key]: value }));
  };

  const updateEditQuestionForm = (key: keyof QuestionForm, value: string | number) => {
    setEditQuestionForm((prev) => ({ ...prev, [key]: value }));
  };

  const beginNewQuiz = () => {
    setQuizForm(EMPTY_QUIZ_FORM);
    setShowNewQuizForm(true);
  };

  const toggleQuestionDetails = (questionId: number | string) => {
    setSelectedQuestionId(questionId);
    setExpandedQuestionId((current) => (current === questionId ? null : questionId));
  };

  const openQuestionUpdate = (questionId: number | string) => {
    setConfirmDeleteAction(null);
    setSelectedQuestionId(questionId);
    setExpandedQuestionId(questionId);
    setQuizDetailsTab('update');
  };

  const handleCreateQuiz = async () => {
    const generatedSlug = slugify(quizForm.title);
    const payload = {
      slug: generatedSlug,
      title: quizForm.title.trim(),
      description: quizForm.description.trim(),
      max_attempts: Number(quizForm.max_attempts) || 2,
    };

    if (!payload.title || !payload.slug) {
      toast.error('Quiz title is required.');
      return;
    }

    setLoading(true);
    try {
      const result = await api.createQuiz(payload);
      setQuizForm(EMPTY_QUIZ_FORM);
      setShowNewQuizForm(false);
      await refreshLibrary(result.item.id);
      setConfirmDeleteAction(null);
      setExpandedQuestionId(null);
      setQuizDetailsTab('create');
      setShowQuizDetails(true);
      toast.success('Quiz created.');
    } catch (err) {
      toast.error(`Failed to create quiz: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteQuiz = async () => {
    if (!selectedQuiz) {
      return;
    }

    setLoading(true);
    try {
      await api.deleteQuiz(selectedQuiz.slug);
      setConfirmDeleteAction(null);
      await refreshLibrary(null);
      setSelectedQuestionId(null);
      setExpandedQuestionId(null);
      setShowQuizDetails(false);
      setQuestions([]);
      toast.success('Quiz deleted.');
    } catch (err) {
      toast.error(`Failed to delete quiz: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleSetActiveQuiz = async () => {
    if (!selectedQuiz) {
      return;
    }

    setLoading(true);
    try {
      const result = await api.updateQuizConfig(selectedQuiz.slug);
      setActiveQuiz(result.active);
      toast.success(`Active quiz set to ${result.active.quiz_title}`);
    } catch (err) {
      toast.error(`Failed to activate quiz: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleExportQuizCatalog = async () => {
    setCatalogExportLoading(true);
    try {
      const { blob, filename } = await api.downloadQuizCatalogExport();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('Quiz catalog exported.');
    } catch (err) {
      toast.error(`Failed to export quiz catalog: ${err}`);
    } finally {
      setCatalogExportLoading(false);
    }
  };

  const handleCreateQuestion = async () => {
    if (!selectedQuiz) {
      return;
    }

    const options = parseOptions(createQuestionForm.optionsText);
    const generatedQuestionKey = slugify(createQuestionForm.question);
    const payload = {
      question_key: generatedQuestionKey,
      question: createQuestionForm.question.trim(),
      options,
      correct_answer: createQuestionForm.correct_answer.trim(),
      hint: createQuestionForm.hint.trim(),
      explanation: createQuestionForm.explanation.trim(),
      order_index: Number(createQuestionForm.order_index) || 0,
    };

    if (
      !payload.question ||
      !payload.question_key ||
      options.length < 2 ||
      !payload.correct_answer ||
      !payload.hint ||
      !payload.explanation
    ) {
      toast.error('Question, 2+ options, correct answer, hint, and explanation are required.');
      return;
    }

    if (!options.includes(payload.correct_answer)) {
      toast.error('Correct answer must match one of the options.');
      return;
    }

    setLoading(true);
    try {
      const result = await api.createQuizQuestion(selectedQuiz.slug, payload);
      await refreshSelectedQuizData(selectedQuiz);
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
    if (!selectedQuestion || !selectedQuiz) {
      return;
    }

    const options = parseOptions(editQuestionForm.optionsText);
    const payload = {
      question_key: selectedQuestion.question_key,
      question: editQuestionForm.question.trim(),
      options,
      correct_answer: editQuestionForm.correct_answer.trim(),
      hint: editQuestionForm.hint.trim(),
      explanation: editQuestionForm.explanation.trim(),
      order_index: Number(editQuestionForm.order_index) || 0,
    };

    if (
      !payload.question ||
      !payload.question_key ||
      options.length < 2 ||
      !payload.correct_answer ||
      !payload.hint ||
      !payload.explanation
    ) {
      toast.error('Question, 2+ options, correct answer, hint, and explanation are required.');
      return;
    }

    if (!options.includes(payload.correct_answer)) {
      toast.error('Correct answer must match one of the options.');
      return;
    }

    setLoading(true);
    try {
      await api.updateQuizQuestion(selectedQuestion.question_key, payload);
      await refreshSelectedQuizData(selectedQuiz);
      toast.success('Question updated.');
    } catch (err) {
      toast.error(`Failed to update question: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteQuestion = async () => {
    if (!selectedQuestion || !selectedQuiz) {
      return;
    }

    setLoading(true);
    try {
      await api.deleteQuizQuestion(selectedQuestion.question_key);
      await refreshSelectedQuizData(selectedQuiz);
      setConfirmDeleteAction(null);
      toast.success('Question deleted.');
    } catch (err) {
      toast.error(`Failed to delete question: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (confirmDeleteAction === 'quiz') {
      await handleDeleteQuiz();
      return;
    }

    if (confirmDeleteAction === 'question') {
      await handleDeleteQuestion();
    }
  };

  const renderQuizDetailsPanel = () => {
    if (!selectedQuiz) {
      return null;
    }

    return (
      <section className="mt-6 rounded-2xl border border-border/50 bg-background/95 shadow-sm">
        <div
          className={`flex flex-wrap items-start justify-between gap-3 bg-muted/30 px-5 py-4 ${
            showQuizDetails ? 'border-b border-border/50' : ''
          }`}
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-semibold">{selectedQuiz.title}</h3>
              {activeQuiz.quiz_slug === selectedQuiz.slug ? (
                <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200">
                  ACTIVE
                </span>
              ) : null}
            </div>
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
              {truncateText(selectedQuiz.description, 220, 'No description yet.')}
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
              onClick={() => setShowQuizDetails((current) => !current)}
              className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
            >
              {showQuizDetails ? 'Hide Details' : 'Show Details'}
            </button>
          </div>
        </div>

        {showQuizDetails ? (
          <div className="bg-background/80 p-5">
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setConfirmDeleteAction(null);
                    setQuizDetailsTab('view');
                  }}
                  className={`rounded-full px-4 py-2 text-sm font-medium ${
                    quizDetailsTab === 'view'
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
                    setQuizDetailsTab('create');
                  }}
                  className={`rounded-full px-4 py-2 text-sm font-medium ${
                    quizDetailsTab === 'create'
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
                    setQuizDetailsTab('update');
                  }}
                  disabled={questions.length === 0}
                  className={`rounded-full px-4 py-2 text-sm font-medium ${
                    quizDetailsTab === 'update'
                      ? 'bg-blue-600 text-white'
                      : 'border border-border bg-background text-foreground'
                  } disabled:opacity-50`}
                >
                  Update
                </button>
              </div>

              {quizDetailsTab === 'view' ? (
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
                            className={`w-full rounded-xl border px-4 py-3 text-left transition ${
                              isExpanded || isEditing
                                ? 'border-blue-400 bg-blue-50 shadow-sm dark:bg-blue-950/30'
                                : 'border-border/60 bg-background hover:border-blue-300 hover:bg-background/90'
                            } cursor-pointer`}
                          >
                            <div className="flex flex-wrap items-start justify-between gap-3">
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium text-muted-foreground">
                                  <span className="rounded-full border border-border/60 bg-muted px-2.5 py-1">
                                    Question {index + 1}
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

                                  <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
                                    <span className="text-xs font-medium text-muted-foreground">Options</span>
                                    <div className="mt-3 flex flex-wrap gap-2">
                                      {question.options.map((option) => (
                                        <span
                                          key={option}
                                          className={`rounded-full px-2.5 py-1 text-xs ${
                                            option === question.correct_answer
                                              ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200'
                                              : 'bg-background text-muted-foreground'
                                          }`}
                                        >
                                          {option}
                                        </span>
                                      ))}
                                    </div>
                                  </div>
                                </div>

                                <div className="space-y-3">
                                  <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
                                    <span className="text-xs font-medium text-muted-foreground">Hint</span>
                                    <p className="mt-2 text-sm leading-6 text-foreground">
                                      {truncateText(question.hint, 220, 'No hint added.')}
                                    </p>
                                  </div>

                                  <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
                                    <span className="text-xs font-medium text-muted-foreground">Explanation</span>
                                    <p className="mt-2 text-sm leading-6 text-foreground">
                                      {truncateText(question.explanation, 220, 'No explanation added.')}
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
                      This quiz has no questions yet.
                    </div>
                  )}
                </section>
              ) : null}

              {quizDetailsTab === 'create' ? (
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

                    <label className="flex flex-col gap-1 text-sm">
                      <span>Options, one per line</span>
                      <textarea
                        value={createQuestionForm.optionsText}
                        onChange={(e) => updateCreateQuestionForm('optionsText', e.target.value)}
                        placeholder={'Option 1\nOption 2\nOption 3'}
                        className="min-h-[96px] rounded-lg border border-border bg-background px-3 py-2"
                      />
                    </label>

                    <label className="flex flex-col gap-1 text-sm">
                      <span>Correct answer</span>
                      <select
                        value={createQuestionForm.correct_answer}
                        onChange={(e) => updateCreateQuestionForm('correct_answer', e.target.value)}
                        className="rounded-lg border border-border bg-background px-3 py-2"
                      >
                        <option value="">Select correct answer...</option>
                        {parsedCreateOptions.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    </label>

                    <div className="grid gap-4 md:grid-cols-2">
                      <label className="flex flex-col gap-1 text-sm">
                        <span>Hint</span>
                        <textarea
                          value={createQuestionForm.hint}
                          onChange={(e) => updateCreateQuestionForm('hint', e.target.value)}
                          placeholder="Add a hint for the user."
                          className="min-h-[72px] rounded-lg border border-border bg-background px-3 py-2"
                        />
                      </label>

                      <label className="flex flex-col gap-1 text-sm">
                        <span>Explanation</span>
                        <textarea
                          value={createQuestionForm.explanation}
                          onChange={(e) => updateCreateQuestionForm('explanation', e.target.value)}
                          placeholder="Add the explanation for the correct answer."
                          className="min-h-[72px] rounded-lg border border-border bg-background px-3 py-2"
                        />
                      </label>
                    </div>
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void handleCreateQuestion()}
                      disabled={loading || !selectedQuiz}
                      className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                    >
                      Create Question
                    </button>
                  </div>
                </section>
              ) : null}

              {quizDetailsTab === 'update' ? (
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

                        <label className="flex flex-col gap-1 text-sm">
                          <span>Options, one per line</span>
                          <textarea
                            value={editQuestionForm.optionsText}
                            onChange={(e) => updateEditQuestionForm('optionsText', e.target.value)}
                            placeholder={'Option 1\nOption 2\nOption 3'}
                            className="min-h-[96px] rounded-lg border border-border bg-background px-3 py-2"
                          />
                        </label>

                        <label className="flex flex-col gap-1 text-sm">
                          <span>Correct answer</span>
                          <select
                            value={editQuestionForm.correct_answer}
                            onChange={(e) => updateEditQuestionForm('correct_answer', e.target.value)}
                            className="rounded-lg border border-border bg-background px-3 py-2"
                          >
                            <option value="">Select correct answer...</option>
                            {parsedEditOptions.map((option) => (
                              <option key={option} value={option}>
                                {option}
                              </option>
                            ))}
                          </select>
                        </label>

                        <div className="grid gap-4 md:grid-cols-2">
                          <label className="flex flex-col gap-1 text-sm">
                            <span>Hint</span>
                            <textarea
                              value={editQuestionForm.hint}
                              onChange={(e) => updateEditQuestionForm('hint', e.target.value)}
                              placeholder="Add a hint for the user."
                              className="min-h-[72px] rounded-lg border border-border bg-background px-3 py-2"
                            />
                          </label>

                          <label className="flex flex-col gap-1 text-sm">
                            <span>Explanation</span>
                            <textarea
                              value={editQuestionForm.explanation}
                              onChange={(e) => updateEditQuestionForm('explanation', e.target.value)}
                              placeholder="Add the explanation for the correct answer."
                              className="min-h-[72px] rounded-lg border border-border bg-background px-3 py-2"
                            />
                          </label>
                        </div>
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
          <h2 className="text-xl font-semibold">Quiz Configuration</h2>
        </div>

        <div className="min-w-[15rem] rounded-xl border border-emerald-200/70 bg-emerald-50/60 px-4 py-3 text-right dark:border-emerald-900/50 dark:bg-emerald-950/20">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">
            Active Quiz
          </div>
          {activeQuiz.quiz_title ? (
            <>
              <div className="mt-1 text-sm font-semibold">{activeQuiz.quiz_title}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                {formatCount(activeQuizQuestionCount, 'question')}
              </div>
            </>
          ) : (
            <div className="mt-1 text-sm text-muted-foreground">No active quiz selected.</div>
          )}
        </div>
      </div>

      <div className="space-y-6">
        <section>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-semibold">Available Quizzes</h3>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handleExportQuizCatalog()}
                disabled={catalogExportLoading || quizzes.length === 0}
                className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium disabled:opacity-50"
              >
                {catalogExportLoading ? 'Exporting...' : 'Export Excel'}
              </button>
              <button
                type="button"
                onClick={beginNewQuiz}
                className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
              >
                Add Quiz
              </button>
            </div>
          </div>

          <div className="mt-4">
          {quizzes.length > 0 ? (
            <div className="space-y-4">
              <div className="space-y-3">
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
                  <label className="flex flex-col gap-1 text-sm">
                    <span>Select quiz</span>
                    <select
                      value={selectedQuiz?.slug ?? ''}
                      onChange={(e) => selectQuiz(e.target.value || null)}
                      className="rounded-lg border border-border bg-background px-3 py-2"
                    >
                      <option value="">Choose a quiz...</option>
                      {quizzes.map((quiz) => (
                          <option key={quiz.slug} value={quiz.slug}>
                          {quiz.title}
                        </option>
                      ))}
                    </select>
                  </label>

                  <button
                    type="button"
                    onClick={() => void handleSetActiveQuiz()}
                    disabled={loading || !selectedQuiz || activeQuiz.quiz_slug === selectedQuiz.slug}
                    className="self-end rounded-full bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  >
                    Set Active
                  </button>

                  <button
                    type="button"
                    onClick={() => setConfirmDeleteAction('quiz')}
                    disabled={loading || !selectedQuiz}
                    className="self-end rounded-full bg-red-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  >
                    Delete Quiz
                  </button>
                </div>

                {confirmDeleteAction === 'quiz' && selectedQuiz ? (
                  <div className="rounded-xl border border-red-200/70 bg-background px-4 py-4 shadow-sm dark:border-red-900/50">
                    <h4 className="text-sm font-semibold text-red-700 dark:text-red-300">Delete this quiz?</h4>
                    <p className="mt-1 text-sm text-muted-foreground">Delete "{selectedQuiz.title}"?</p>
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
                No quizzes found in the local catalog yet.
              </div>
            )}
          </div>
        </section>
      </div>

      {showNewQuizForm ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-2xl overflow-hidden rounded-2xl border border-slate-200 shadow-2xl dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 bg-slate-50 px-5 py-4 dark:border-zinc-700 dark:bg-zinc-900">
              <div className="min-w-0">
                <h3 className="text-lg font-semibold">Add Quiz</h3>
              </div>

              <button
                type="button"
                onClick={() => setShowNewQuizForm(false)}
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
                    value={quizForm.title}
                    onChange={(e) => updateQuizForm('title', e.target.value)}
                    className="rounded-lg border border-border bg-background px-3 py-2"
                  />
                </label>

                <label className="flex flex-col gap-1 text-sm">
                  <span>Description</span>
                  <textarea
                    value={quizForm.description}
                    onChange={(e) => updateQuizForm('description', e.target.value)}
                    className="min-h-[100px] rounded-lg border border-border bg-background px-3 py-2"
                  />
                </label>

                <label className="flex flex-col gap-1 text-sm">
                  <span>Max attempts</span>
                  <input
                    type="number"
                    min={1}
                    value={quizForm.max_attempts}
                    onChange={(e) => updateQuizForm('max_attempts', Number(e.target.value))}
                    className="rounded-lg border border-border bg-background px-3 py-2"
                  />
                </label>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void handleCreateQuiz()}
                  disabled={loading}
                  className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
                >
                  Create Quiz
                </button>
                <button
                  type="button"
                  onClick={beginNewQuiz}
                  className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium"
                >
                  Reset
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {renderQuizDetailsPanel()}
    </section>
  );
}

export default QuizManagementTab;
