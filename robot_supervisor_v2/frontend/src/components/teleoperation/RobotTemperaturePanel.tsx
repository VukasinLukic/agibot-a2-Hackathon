import type { RobotTemperatureState, Service } from '@/api/types';

const MOTOR_NAME_MAP: Record<number, string> = {
  0: 'LeftHipPitch',
  1: 'LeftHipRoll',
  2: 'LeftHipYaw',
  3: 'LeftKnee',
  4: 'LeftAnklePitch',
  5: 'LeftAnkleRoll',
  6: 'RightHipPitch',
  7: 'RightHipRoll',
  8: 'RightHipYaw',
  9: 'RightKnee',
  10: 'RightAnklePitch',
  11: 'RightAnkleRoll',
  12: 'WaistYaw',
  13: 'WaistRoll',
  14: 'WaistPitch',
  15: 'LeftShoulderPitch',
  16: 'LeftShoulderRoll',
  17: 'LeftShoulderYaw',
  18: 'LeftElbow',
  19: 'LeftWristRoll',
  20: 'LeftWristPitch',
  21: 'LeftWristYaw',
  22: 'RightShoulderPitch',
  23: 'RightShoulderRoll',
  24: 'RightShoulderYaw',
  25: 'RightElbow',
  26: 'RightWristRoll',
  27: 'RightWristPitch',
  28: 'RightWristYaw',
};

const PRIORITY_MOTORS = [
  { index: 13, name: 'WaistRoll' },
  { index: 14, name: 'WaistPitch' },
  { index: 22, name: 'RightShoulderPitch' },
  { index: 15, name: 'LeftShoulderPitch' },
] as const;

interface RobotTemperaturePanelProps {
  service?: Service;
  temperatureState: RobotTemperatureState | null;
  loading: boolean;
  onToggleService: (serviceName: string, currentState: string) => void;
  onViewLogs: (serviceName: string) => void;
  bare?: boolean;
}

function formatTemperature(value: number | null | undefined) {
  return typeof value === 'number' ? `${value.toFixed(1)}°C` : 'N/A';
}

function formatTemperatureReadings(values: number[] | undefined, fallback: number | null | undefined) {
  if (values && values.length > 0) {
    return `${values.map((value) => value.toFixed(1)).join(' / ')}°C`;
  }
  return formatTemperature(fallback);
}

function getMotorName(index: number, fallbackLabel?: string) {
  return MOTOR_NAME_MAP[index] ?? fallbackLabel ?? `motor[${index.toString().padStart(2, '0')}]`;
}

function formatAge(value: number | null | undefined) {
  if (typeof value !== 'number') {
    return 'Waiting for data';
  }
  if (value < 1) {
    return 'Live';
  }
  return `${value.toFixed(1)}s ago`;
}

function getServiceActionLabel(state: Service['state']) {
  if (state === 'running') {
    return 'Stop';
  }
  if (state === 'failed') {
    return 'Retry';
  }
  return 'Start';
}

function getTemperatureTone(
  temperature: number | null | undefined,
  warningThreshold: number,
  criticalThreshold: number,
) {
  if (typeof temperature !== 'number') {
    return 'border-border/60 bg-muted/40 text-foreground';
  }
  if (temperature >= criticalThreshold) {
    return 'border-red-300 bg-red-50 text-red-900 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-100';
  }
  if (temperature >= warningThreshold) {
    return 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100';
  }
  return 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-100';
}

export function RobotTemperaturePanel({
  service,
  temperatureState,
  loading,
  onToggleService,
  onViewLogs,
  bare = false,
}: RobotTemperaturePanelProps) {
  if (!service) {
    const notConfiguredContent = (
      <>
        <div className="mb-3">
          <h3 className="text-lg font-semibold">Robot temperatures</h3>
          <p className="text-xs text-muted-foreground">
            Add the <span className="font-mono">robot-temperature-monitor</span> service to the supervisor config to stream motor temperatures here.
          </p>
        </div>
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          Temperature monitoring is not configured in the backend.
        </div>
      </>
    );
    if (bare) return <div>{notConfiguredContent}</div>;
    return (
      <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
        {notConfiguredContent}
      </section>
    );
  }

  const warningThreshold = temperatureState?.warn_threshold_c ?? 65;
  const criticalThreshold = temperatureState?.critical_threshold_c ?? 80;
  const hasMotors = (temperatureState?.motors.length ?? 0) > 0;
  const stateLabel = service.state.toUpperCase();
  const priorityMotorIndexes = new Set<number>(PRIORITY_MOTORS.map((motor) => motor.index));
  const motors = temperatureState?.motors ?? [];
  const priorityMotors = PRIORITY_MOTORS.map((priorityMotor) => ({
    ...priorityMotor,
    motor: motors.find((motor) => motor.index === priorityMotor.index) ?? null,
  }));
  const remainingMotors = motors.filter((motor) => !priorityMotorIndexes.has(motor.index));

  const innerContent = (
    <>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold">Robot temperatures</h3>
          <p className="text-xs text-muted-foreground">
            Low-state DDS snapshot from <span className="font-mono">{temperatureState?.topic ?? 'rt/lowstate'}</span>.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={() => onViewLogs(service.name)}
            className="rounded-full border border-border/30 bg-background/20 px-2 py-0.5 text-[11px] text-muted-foreground/55 transition-colors hover:border-border/55 hover:bg-background/45 hover:text-muted-foreground/80"
            title="View logs"
          >
            View Logs
          </button>
          <button
            onClick={() => onToggleService(service.name, service.state)}
            disabled={loading}
            className={`rounded-full px-3 py-1 text-xs font-medium text-white transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              service.state === 'running' ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700'
            }`}
          >
            {loading ? '...' : getServiceActionLabel(service.state)}
          </button>
        </div>
      </div>

      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-border/60 bg-muted/40 p-4">
          <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Service</p>
          <div className="mt-2 flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                service.state === 'running'
                  ? 'bg-emerald-500'
                  : service.state === 'failed'
                  ? 'bg-red-500'
                  : service.state === 'starting' || service.state === 'stopping'
                  ? 'bg-blue-500'
                  : 'bg-amber-500'
              }`}
            />
            <span className="font-mono text-sm font-semibold">{stateLabel}</span>
          </div>
        </div>
        <div className="rounded-xl border border-border/60 bg-muted/40 p-4">
          <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Last update</p>
          <p className="mt-2 text-sm font-semibold">{formatAge(temperatureState?.message_age_seconds)}</p>
        </div>
        <div className="rounded-xl border border-border/60 bg-muted/40 p-4">
          <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Hottest motor</p>
          <p className="mt-2 text-sm font-semibold">
            {temperatureState?.hottest_motor
              ? `${getMotorName(temperatureState.hottest_motor.index, temperatureState.hottest_motor.label)} · ${formatTemperature(temperatureState.hottest_motor.temperature_c)}`
              : 'No data'}
          </p>
        </div>
        <div className="rounded-xl border border-border/60 bg-muted/40 p-4">
          <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Thresholds</p>
          <p className="mt-2 text-sm font-semibold">
            Warn {warningThreshold}°C · Critical {criticalThreshold}°C
          </p>
        </div>
      </div>

      {temperatureState?.last_error && service.state !== 'running' && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
          {temperatureState.last_error}
        </div>
      )}

      {hasMotors ? (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>{temperatureState?.motor_count ?? 0} motors</span>
            <span>{temperatureState?.warning_count ?? 0} in warning band</span>
            <span>{temperatureState?.critical_count ?? 0} in critical band</span>
          </div>
          <div className="mb-6">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h4 className="text-sm font-semibold">Priority motors</h4>
              <span className="text-xs text-muted-foreground">13 WaistRoll, 14 WaistPitch, 22 RightShoulderPitch, 15 LeftShoulderPitch</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {priorityMotors.map(({ index, name, motor }) => (
                <div
                  key={index}
                  className={`rounded-xl border p-4 ${getTemperatureTone(
                    motor?.temperature_c,
                    warningThreshold,
                    criticalThreshold,
                  )}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold">{name}</p>
                      <p className="text-xs opacity-70">motor[{index.toString().padStart(2, '0')}]</p>
                    </div>
                    <p className="text-lg font-semibold">
                      {formatTemperatureReadings(motor?.temperature_readings_c, motor?.temperature_c)}
                    </p>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-3 text-xs opacity-80">
                    <span>Mode: {motor?.mode ?? 'n/a'}</span>
                    <span>q: {typeof motor?.q === 'number' ? motor.q.toFixed(3) : 'n/a'}</span>
                    <span>dq: {typeof motor?.dq === 'number' ? motor.dq.toFixed(3) : 'n/a'}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-3 flex items-center justify-between gap-3">
              <h4 className="text-sm font-semibold">All other motors</h4>
              <span className="text-xs text-muted-foreground">{remainingMotors.length} motors</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {remainingMotors.map((motor) => (
              <div
                key={motor.index}
                className={`rounded-xl border p-4 ${getTemperatureTone(
                  motor.temperature_c,
                  warningThreshold,
                  criticalThreshold,
                )}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold">{getMotorName(motor.index, motor.label)}</p>
                    <p className="text-xs opacity-70">Index {motor.index}</p>
                  </div>
                  <p className="text-lg font-semibold">
                    {formatTemperatureReadings(motor.temperature_readings_c, motor.temperature_c)}
                  </p>
                </div>
                <div className="mt-3 flex flex-wrap gap-3 text-xs opacity-80">
                  <span>Mode: {motor.mode ?? 'n/a'}</span>
                  <span>q: {typeof motor.q === 'number' ? motor.q.toFixed(3) : 'n/a'}</span>
                  <span>dq: {typeof motor.dq === 'number' ? motor.dq.toFixed(3) : 'n/a'}</span>
                </div>
              </div>
              ))}
            </div>
          </div>
        </>
      ) : (
        <div className="rounded-xl border border-border/60 bg-muted/40 p-4 text-sm text-muted-foreground">
          {service.state === 'running'
            ? 'Temperature monitor is running and waiting for the first low-state sample.'
            : 'Start the temperature monitor to begin streaming robot motor temperatures.'}
        </div>
      )}
    </>
  );

  if (bare) return <div>{innerContent}</div>;
  return (
    <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
      {innerContent}
    </section>
  );
}
