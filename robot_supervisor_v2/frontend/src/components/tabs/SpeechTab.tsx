import { type ChangeEvent, useEffect, useMemo, useState } from 'react';
import { Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type {
  PromptItem,
  SpeechBackgroundAudioConfig,
  SpeechBackgroundAudioOptionsResponse,
  SpeechConfigResponse,
  SpeechVoiceConfig,
  SpeechVoiceOptionsResponse,
} from '@/api/types';

interface PronunciationRow {
  id: string;
  source: string;
  replacement: string;
}

const DEFAULT_BACKGROUND_AUDIO: SpeechBackgroundAudioConfig = {
  enabled: false,
  source_type: 'builtin',
  source: 'KEYBOARD_TYPING',
  volume: 0.45,
};

const EMPTY_BACKGROUND_AUDIO_OPTIONS: SpeechBackgroundAudioOptionsResponse = {
  built_in: [],
  uploads: [],
  accepted_extensions: [],
};

function makeId() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function pronunciationRowsFromConfig(config: SpeechConfigResponse | null): PronunciationRow[] {
  const transformations = dataIsEnglish(config)
    ? config?.english_transformations
    : config?.custom_transformations;
  return Object.entries(transformations ?? {}).map(([source, replacement]) => ({
    id: makeId(),
    source,
    replacement,
  }));
}

// Must mirror the running agent, not the selected voice: with the English
// canary on, a Serbian-language voice still reads the English pronunciation and
// persona lists, so guessing from active_voice edits the list nobody applies.
function dataIsEnglish(config: SpeechConfigResponse | null): boolean {
  return Boolean(config?.english_locale_active);
}

function normalizeVoices(voices: SpeechVoiceConfig[]): SpeechVoiceConfig[] {
  const seen = new Set<string>();
  const normalized: SpeechVoiceConfig[] = [];
  for (const voice of voices) {
    const normalizedVoice = {
      provider: voice.provider.trim().toLowerCase(),
      model: voice.model.trim(),
      voice_id: voice.voice_id.trim(),
      label: voice.label.trim(),
      language: voice.language.trim(),
    };
    const identity = voiceKey(normalizedVoice);
    if (!normalizedVoice.provider || seen.has(identity)) {
      continue;
    }
    normalized.push({
      ...normalizedVoice,
      label: normalizedVoice.label || normalizedVoice.voice_id || normalizedVoice.model || normalizedVoice.provider,
    });
    seen.add(identity);
  }
  return normalized;
}

function voiceKey(voice?: SpeechVoiceConfig | null): string {
  if (!voice) return '';
  return [voice.provider, voice.model, voice.voice_id, voice.language].map((value) => value.trim()).join('|');
}

function voicesFromResponse(
  config: SpeechConfigResponse,
  voiceResponse: SpeechVoiceOptionsResponse
): SpeechVoiceConfig[] {
  const providerVoices = normalizeVoices(voiceResponse.voices ?? []);
  if (providerVoices.length > 0) {
    return providerVoices;
  }
  return normalizeVoices(config.voices ?? []);
}

export function SpeechTab() {
  const [config, setConfig] = useState<SpeechConfigResponse | null>(null);
  const [activeVoice, setActiveVoice] = useState('');
  const [initialGreeting, setInitialGreeting] = useState('');
  const [goodbyeText, setGoodbyeText] = useState('');
  const [personas, setPersonas] = useState<PromptItem[]>([]);
  const [activePersona, setActivePersona] = useState('');
  const [personaTitle, setPersonaTitle] = useState('');
  const [personaPrompt, setPersonaPrompt] = useState('');
  const [voices, setVoices] = useState<SpeechVoiceConfig[]>([]);
  const [voiceSource, setVoiceSource] = useState<SpeechVoiceOptionsResponse['source']>('configured_fallback');
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [pronunciations, setPronunciations] = useState<PronunciationRow[]>([]);
  const [backgroundAudio, setBackgroundAudio] = useState<SpeechBackgroundAudioConfig>(DEFAULT_BACKGROUND_AUDIO);
  const [backgroundOptions, setBackgroundOptions] = useState<SpeechBackgroundAudioOptionsResponse>(EMPTY_BACKGROUND_AUDIO_OPTIONS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);

  const activeVoiceExists = useMemo(
    () => voices.some((voice) => voiceKey(voice) === activeVoice.trim()),
    [activeVoice, voices]
  );

  async function loadConfig() {
    setLoading(true);
    try {
      const [data, voiceResponse, options] = await Promise.all([
        api.getSpeechConfig(),
        api.getSpeechVoices(),
        api.getSpeechBackgroundAudioOptions(),
      ]);
      const personaResponse = await api.listPersonas(
        true,
        dataIsEnglish(data) ? 'en' : undefined,
      );
      const selectedPersona = personaResponse.items.find(
        (item) => item.slug === personaResponse.active,
      );
      const availableVoices = voicesFromResponse(data, voiceResponse);
      setConfig(data);
      setActiveVoice(voiceKey(data.active_voice) || voiceKey(availableVoices[0]));
      setInitialGreeting(data.initial_greeting || '');
      setGoodbyeText(data.goodbye_text || '');
      setPersonas(personaResponse.items);
      setActivePersona(personaResponse.active);
      setPersonaTitle(selectedPersona?.title ?? '');
      setPersonaPrompt(selectedPersona?.prompt_text ?? '');
      setVoices(availableVoices);
      setVoiceSource(voiceResponse.source);
      setVoiceError(voiceResponse.error);
      setPronunciations(pronunciationRowsFromConfig(data));
      setBackgroundAudio(data.background_audio ?? DEFAULT_BACKGROUND_AUDIO);
      setBackgroundOptions(options);
    } catch (err) {
      toast.error(`Failed to load speech config: ${err}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadConfig();
  }, []);

  function updatePronunciation(id: string, updates: Partial<PronunciationRow>) {
    setPronunciations((current) =>
      current.map((row) => (row.id === id ? { ...row, ...updates } : row))
    );
  }

  function removePronunciation(id: string) {
    setPronunciations((current) => current.filter((row) => row.id !== id));
  }

  function addVoice() {
    const voice: SpeechVoiceConfig = {
      provider: 'elevenlabs',
      model: 'eleven_flash_v2_5',
      voice_id: '',
      label: 'New English voice',
      language: 'en',
    };
    setVoices((current) => [...current, voice]);
    setActiveVoice(voiceKey(voice));
  }

  function updateVoice(index: number, updates: Partial<SpeechVoiceConfig>) {
    setVoices((current) => {
      const oldVoice = current[index];
      const nextVoice = { ...oldVoice, ...updates };
      const next = current.map((voice, voiceIndex) => (voiceIndex === index ? nextVoice : voice));
      if (voiceKey(oldVoice) === activeVoice) {
        setActiveVoice(voiceKey(nextVoice));
      }
      return next;
    });
  }

  function removeVoice(index: number) {
    setVoices((current) => {
      const removed = current[index];
      const next = current.filter((_, voiceIndex) => voiceIndex !== index);
      if (voiceKey(removed) === activeVoice) {
        setActiveVoice(voiceKey(next[0]));
      }
      return next;
    });
  }

  function updateBackgroundAudio(updates: Partial<SpeechBackgroundAudioConfig>) {
    setBackgroundAudio((current) => {
      const next = { ...current, ...updates };
      if (updates.source_type) {
        const options = updates.source_type === 'builtin' ? backgroundOptions.built_in : backgroundOptions.uploads;
        next.source = options[0]?.source ?? '';
      }
      return next;
    });
  }

  async function selectPersona(slug: string) {
    const persona = personas.find((item) => item.slug === slug);
    if (!persona || persona.slug === activePersona) return;
    setSaving(true);
    try {
      const result = await api.activatePersona(persona.id);
      setActivePersona(result.active.persona);
      setPersonaTitle(persona.title);
      setPersonaPrompt(persona.prompt_text);
      const speech = await api.getSpeechConfig();
      setConfig(speech);
      setInitialGreeting(speech.initial_greeting || '');
      setGoodbyeText(speech.goodbye_text || '');
      toast.success(
        result.runtime_applied
          ? 'Persona selected and applied to the live agent'
          : 'Persona selected; it will apply to the next conversation',
      );
      window.dispatchEvent(new CustomEvent('prompt-config-updated'));
    } catch (err) {
      toast.error(`Failed to select persona: ${err}`);
    } finally {
      setSaving(false);
    }
  }

  async function uploadBackgroundAudio(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) {
      return;
    }

    setUploading(true);
    try {
      const result = await api.uploadSpeechBackgroundAudio(file);
      setBackgroundOptions(result.options);
      setBackgroundAudio((current) => ({
        ...current,
        enabled: true,
        source_type: 'upload',
        source: result.file.source,
      }));
      toast.success('Background audio uploaded');
    } catch (err) {
      toast.error(`Failed to upload background audio: ${err}`);
    } finally {
      setUploading(false);
    }
  }

  async function saveConfig() {
    const cleanVoices = voices.map((voice) => ({
      provider: voice.provider.trim().toLowerCase(),
      model: voice.model.trim(),
      voice_id: voice.voice_id.trim(),
      label: voice.label.trim(),
      language: voice.language.trim(),
    }));
    const cleanActiveVoice = activeVoice.trim();
    const activeVoiceConfig = cleanVoices.find((voice) => voiceKey(voice) === cleanActiveVoice);
    const customTransformations: Record<string, string> = {};

    for (const row of pronunciations) {
      const source = row.source.trim();
      const replacement = row.replacement.trim();
      if (!source || !replacement) {
        toast.error('Pronunciation rows need both text and replacement');
        return;
      }
      customTransformations[source] = replacement;
    }

    if (!activeVoiceConfig) {
      toast.error('Select an available provider voice');
      return;
    }

    if (cleanVoices.some((voice) => !voice.label || !voice.provider)) {
      toast.error('Available voice data is incomplete');
      return;
    }

    let cleanBackgroundAudio: SpeechBackgroundAudioConfig = {
      enabled: Boolean(backgroundAudio.enabled),
      source_type: backgroundAudio.source_type,
      source: backgroundAudio.source.trim(),
      volume: Math.max(0, Math.min(1, Number(backgroundAudio.volume) || 0)),
    };

    if (!cleanBackgroundAudio.enabled && !cleanBackgroundAudio.source) {
      cleanBackgroundAudio = { ...DEFAULT_BACKGROUND_AUDIO, volume: cleanBackgroundAudio.volume };
    }

    if (cleanBackgroundAudio.enabled && !cleanBackgroundAudio.source) {
      toast.error('Select a thinking background audio source');
      return;
    }

    setSaving(true);
    try {
      const selectedPersona = personas.find((item) => item.slug === activePersona);
      if (selectedPersona) {
        const updatedPersona = await api.updatePersona(
          selectedPersona.id,
          {
            slug: selectedPersona.slug,
            title: personaTitle.trim() || selectedPersona.title,
            prompt_text: personaPrompt.trim(),
            initial_greeting: initialGreeting.trim(),
            goodbye_text: goodbyeText.trim(),
            is_archived: selectedPersona.is_archived,
          },
          dataIsEnglish(config) ? 'en' : undefined,
        );
        setPersonas((current) =>
          current.map((item) =>
            item.slug === selectedPersona.slug ? updatedPersona.item : item,
          ),
        );
      }
      const data = await api.updateSpeechConfig({
        active_voice: activeVoiceConfig,
        initial_greeting: initialGreeting.trim(),
        goodbye_text: goodbyeText.trim(),
        voices: cleanVoices,
        custom_transformations: dataIsEnglish(config) ? (config?.custom_transformations ?? {}) : customTransformations,
        english_transformations: dataIsEnglish(config) ? customTransformations : (config?.english_transformations ?? {}),
        background_audio: cleanBackgroundAudio,
      });
      setConfig(data);
      setActiveVoice(voiceKey(data.active_voice));
      setInitialGreeting(data.initial_greeting || '');
      setGoodbyeText(data.goodbye_text || '');
      setVoices(normalizeVoices(data.voices ?? []));
      setPronunciations(pronunciationRowsFromConfig(data));
      setBackgroundAudio(data.background_audio ?? DEFAULT_BACKGROUND_AUDIO);

      if (data.restarted_services.includes('voice-agent')) {
        toast.success('Speech config saved and voice agent restarted');
      } else {
        toast.success('Speech config saved');
      }
    } catch (err) {
      toast.error(`Failed to save speech config: ${err}`);
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
        <div className="text-sm text-muted-foreground">Loading speech config...</div>
      </section>
    );
  }

  const activeBackgroundOptions =
    backgroundAudio.source_type === 'builtin' ? backgroundOptions.built_in : backgroundOptions.uploads;
  const backgroundSourceExists = activeBackgroundOptions.some((option) => option.source === backgroundAudio.source);
  const uploadAccept = backgroundOptions.accepted_extensions.join(',');

  return (
    <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">Speech</h2>
          <p className="text-xs text-muted-foreground">
            Provider voice and locale-specific pronunciation changes apply after the voice agent restarts.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void loadConfig()}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-full border border-border bg-muted px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted/80 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            Reload
          </button>
          <button
            type="button"
            onClick={() => void saveConfig()}
            disabled={saving || !activeVoiceExists}
            className="inline-flex items-center gap-2 rounded-full bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Save className="h-4 w-4" aria-hidden="true" />
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="space-y-6">
          <div className="space-y-3 rounded-lg border border-border/60 bg-muted/30 p-4">
            <div>
              <h3 className="text-sm font-semibold">Conversation persona</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Select the active persona, or edit its instructions for this language.
              </p>
            </div>
            <label className="block text-xs font-medium" htmlFor="active-persona">
              Persona
              <select
                id="active-persona"
                value={activePersona}
                onChange={(event) => void selectPersona(event.target.value)}
                disabled={saving || personas.length === 0}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              >
                {personas.length === 0 ? <option value="">No personas configured</option> : null}
                {personas.map((persona) => (
                  <option key={persona.slug} value={persona.slug} disabled={persona.is_archived}>
                    {persona.title}{persona.is_archived ? ' — archived' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs font-medium" htmlFor="persona-title">
              Persona title
              <input
                id="persona-title"
                value={personaTitle}
                onChange={(event) => setPersonaTitle(event.target.value)}
                disabled={saving || !activePersona}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm disabled:opacity-50"
              />
            </label>
            <label className="block text-xs font-medium" htmlFor="persona-prompt">
              Persona instructions
              <textarea
                id="persona-prompt"
                value={personaPrompt}
                onChange={(event) => setPersonaPrompt(event.target.value)}
                disabled={saving || !activePersona}
                rows={12}
                className="mt-1 w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-xs disabled:opacity-50"
              />
            </label>
          </div>

          <div className="space-y-3">
            <label className="block text-sm font-medium" htmlFor="active-voice">
              Voice
            </label>
            <select
              id="active-voice"
              value={activeVoice}
              onChange={(event) => setActiveVoice(event.target.value)}
              disabled={saving || voices.length === 0}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
            >
              {voices.length === 0 ? (
                <option value="">No provider voices configured</option>
              ) : (
                voices.map((voice) => (
                  <option key={voiceKey(voice)} value={voiceKey(voice)}>
                    {voice.label} — {voice.provider} / {voice.model || 'default'} / {voice.language || 'unspecified'}
                  </option>
                ))
              )}
            </select>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>{voiceSource === 'configured' ? 'Provider-aware saved voices' : 'Using saved voice fallback'}</span>
              {voiceError && <span className="text-amber-600">{voiceError}</span>}
            </div>
            <div className="space-y-2 rounded-md border border-border/60 p-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs font-semibold">Configured provider voices</span>
                <button
                  type="button"
                  onClick={addVoice}
                  disabled={saving}
                  className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted/80 disabled:opacity-50"
                >
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add voice
                </button>
              </div>
              {voices.map((voice, index) => (
                <div key={`${voiceKey(voice)}-${index}`} className="grid gap-2 rounded-md bg-muted/30 p-2 sm:grid-cols-2">
                  <select
                    value={voice.provider}
                    onChange={(event) => updateVoice(index, { provider: event.target.value })}
                    disabled={saving}
                    className="rounded-md border border-border bg-background px-2 py-1.5 text-xs"
                  >
                    <option value="elevenlabs">ElevenLabs</option>
                    <option value="soniox">Soniox</option>
                    <option value="cartesia">Cartesia</option>
                    <option value="deepgram_aura2">Deepgram Aura-2</option>
                    <option value="truebar">Truebar legacy rollback</option>
                  </select>
                  <input value={voice.label} onChange={(event) => updateVoice(index, { label: event.target.value })} placeholder="Label" disabled={saving} className="rounded-md border border-border bg-background px-2 py-1.5 text-xs" />
                  <input value={voice.model} onChange={(event) => updateVoice(index, { model: event.target.value })} placeholder="Model" disabled={saving} className="rounded-md border border-border bg-background px-2 py-1.5 text-xs" />
                  <input value={voice.voice_id} onChange={(event) => updateVoice(index, { voice_id: event.target.value })} placeholder="Voice ID (blank until audition)" disabled={saving} className="rounded-md border border-border bg-background px-2 py-1.5 text-xs" />
                  <input value={voice.language} onChange={(event) => updateVoice(index, { language: event.target.value })} placeholder="Language, e.g. en" disabled={saving} className="rounded-md border border-border bg-background px-2 py-1.5 text-xs" />
                  <button type="button" onClick={() => removeVoice(index)} disabled={saving || voices.length <= 1} className="inline-flex items-center justify-center gap-1 rounded-md border border-border px-2 py-1.5 text-xs text-destructive disabled:opacity-50">
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Remove
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-3 rounded-lg border border-border/60 bg-muted/30 p-4">
            <div>
              <h3 className="text-sm font-semibold">Conversation opening and closing</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Stored on the active persona and applied after the voice agent restarts.
              </p>
            </div>
            <label className="block text-xs font-medium" htmlFor="initial-greeting">
              Initial greeting
              <textarea
                id="initial-greeting"
                value={initialGreeting}
                onChange={(event) => setInitialGreeting(event.target.value)}
                disabled={saving || !activePersona}
                rows={3}
                className="mt-1 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm disabled:opacity-50"
              />
            </label>
            <label className="block text-xs font-medium" htmlFor="goodbye-text">
              Goodbye text
              <textarea
                id="goodbye-text"
                value={goodbyeText}
                onChange={(event) => setGoodbyeText(event.target.value)}
                disabled={saving || !activePersona}
                rows={3}
                className="mt-1 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm disabled:opacity-50"
              />
            </label>
          </div>

          <div className="space-y-3 rounded-lg border border-border/60 bg-muted/30 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-sm font-semibold">Thinking background audio</h3>
              <label className="inline-flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <input
                  type="checkbox"
                  checked={backgroundAudio.enabled}
                  onChange={(event) => updateBackgroundAudio({ enabled: event.target.checked })}
                  disabled={saving}
                  className="h-4 w-4 rounded border-border"
                />
                Enabled
              </label>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground" htmlFor="background-source-type">
                  Source type
                </label>
                <select
                  id="background-source-type"
                  value={backgroundAudio.source_type}
                  onChange={(event) =>
                    updateBackgroundAudio({ source_type: event.target.value as SpeechBackgroundAudioConfig['source_type'] })
                  }
                  disabled={saving || !backgroundAudio.enabled}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
                >
                  <option value="builtin">Built-in</option>
                  <option value="upload">Upload</option>
                </select>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground" htmlFor="background-source">
                  Source
                </label>
                <select
                  id="background-source"
                  value={backgroundAudio.source}
                  onChange={(event) => updateBackgroundAudio({ source: event.target.value })}
                  disabled={saving || !backgroundAudio.enabled || activeBackgroundOptions.length === 0}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
                >
                  {!backgroundSourceExists && backgroundAudio.source && (
                    <option value={backgroundAudio.source}>{backgroundAudio.source}</option>
                  )}
                  {activeBackgroundOptions.length === 0 ? (
                    <option value="">No sources available</option>
                  ) : (
                    activeBackgroundOptions.map((option) => (
                      <option key={option.source} value={option.source}>
                        {option.label}
                      </option>
                    ))
                  )}
                </select>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground" htmlFor="background-volume">
                  Volume
                </label>
                <div className="flex items-center gap-3">
                  <input
                    id="background-volume"
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={backgroundAudio.volume}
                    onChange={(event) => updateBackgroundAudio({ volume: Number(event.target.value) })}
                    disabled={saving || !backgroundAudio.enabled}
                    className="min-w-0 flex-1"
                  />
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={backgroundAudio.volume}
                    onChange={(event) => updateBackgroundAudio({ volume: Number(event.target.value) })}
                    disabled={saving || !backgroundAudio.enabled}
                    className="w-20 rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
                  />
                </div>
              </div>

              <label className="inline-flex cursor-pointer items-end">
                <span className="rounded-full border border-border bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50">
                  {uploading ? 'Uploading...' : 'Upload'}
                </span>
                <input
                  type="file"
                  accept={uploadAccept}
                  onChange={(event) => void uploadBackgroundAudio(event)}
                  disabled={saving || uploading}
                  className="sr-only"
                />
              </label>
            </div>

            <p className="text-xs text-muted-foreground">
              Thinking audio changes apply after saving and restarting the voice agent.
            </p>
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold">Configured pronunciation</h3>
            <button
              type="button"
              onClick={() =>
                setPronunciations((current) => [
                  ...current,
                  { id: makeId(), source: '', replacement: '' },
                ])
              }
              disabled={saving}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted/80 disabled:opacity-50"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              Add
            </button>
          </div>

          <div className="space-y-2">
            {pronunciations.map((row) => (
              <div key={row.id} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] gap-2">
                <input
                  value={row.source}
                  onChange={(event) => updatePronunciation(row.id, { source: event.target.value })}
                  placeholder="Text"
                  disabled={saving}
                  className="min-w-0 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
                />
                <input
                  value={row.replacement}
                  onChange={(event) => updatePronunciation(row.id, { replacement: event.target.value })}
                  placeholder="Replacement"
                  disabled={saving}
                  className="min-w-0 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={() => removePronunciation(row.id)}
                  disabled={saving}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-border bg-background text-muted-foreground transition-colors hover:text-red-500 disabled:opacity-50"
                  aria-label="Remove pronunciation"
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      {config && (
        <div className="mt-6 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span>Voice agent: {config.service_state}</span>
          <span className="font-mono">
            {config.active_voice
              ? `${config.active_voice.provider}/${config.active_voice.model || 'default'}/${config.active_voice.label}/${config.active_voice.language || 'unspecified'}`
              : 'No active voice'}
          </span>
          <span>{Object.keys(config.effective_transformations).length} effective pronunciations</span>
        </div>
      )}
    </section>
  );
}

export default SpeechTab;
