import { CollapsibleSection } from '@/components/ui/CollapsibleSection';
import { CameraMonitorSection } from '@/components/conversation/CameraMonitorSection';
import { RobotTemperaturePanel } from '@/components/teleoperation/RobotTemperaturePanel';
import { ServiceConfigRow } from '@/components/services/ServiceConfigRow';
import type { Service, RobotTemperatureState, TeleoperationRuntimeStatus } from '@/api/types';

interface TeleoperationTabProps {
  teleimagerService: Service | undefined;
  inspireHandsService: Service | undefined;
  xrTeleopService: Service | undefined;
  cameraBridgeService: Service | undefined;
  temperatureMonitorService: Service | undefined;
  robotTemperature: RobotTemperatureState | null;
  teleopRuntime: TeleoperationRuntimeStatus | null;
  loading: string | null;
  getServiceDisplayName: (service: Service) => string;
  getServiceStartConflict: (serviceName: string) => string | null;
  canToggleService: (service: Service) => boolean;
  getServiceActionLabel: (service: Service) => string;
  onToggleService: (serviceName: string, currentState: string) => void;
  onStartTeleoperation: () => void;
  onStopTeleoperation: () => void;
  onViewLogs: (serviceName: string) => void;
}

export function TeleoperationTab({
  teleimagerService,
  inspireHandsService,
  xrTeleopService,
  cameraBridgeService,
  temperatureMonitorService,
  robotTemperature,
  teleopRuntime,
  loading,
  getServiceDisplayName,
  getServiceStartConflict,
  canToggleService,
  getServiceActionLabel,
  onToggleService,
  onStartTeleoperation,
  onStopTeleoperation,
  onViewLogs,
}: TeleoperationTabProps) {
  const teleServices = [
    teleimagerService,
    inspireHandsService,
    xrTeleopService,
    cameraBridgeService,
  ].filter((s): s is Service => Boolean(s));

  return (
    <div className="space-y-4">
      {/* Section 1: Teleoperation Services */}
      <CollapsibleSection title="Teleoperation Services">
        <p className="mb-4 text-xs text-muted-foreground">
          Start the teleimager video server in the dedicated <span className="font-mono">teleimager</span> conda environment.
        </p>
        <div className="space-y-3">
          {teleServices.map((service) => {
            const startConflict = getServiceStartConflict(service.name);
            const canToggle = canToggleService(service);

            return (
              <div
                key={service.name}
                className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border/60 bg-muted/50 p-4"
              >
                <div className="flex items-center gap-3">
                  <span className="font-medium">{getServiceDisplayName(service)}</span>
                  <button
                    onClick={() => onViewLogs(service.name)}
                    className="rounded-full border border-border/30 bg-background/20 px-2 py-0.5 text-[11px] text-muted-foreground/55 transition-colors hover:border-border/55 hover:bg-background/45 hover:text-muted-foreground/80"
                    title="View logs"
                  >
                    View Logs
                  </button>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <ServiceConfigRow
                    serviceName={service.name}
                    serviceState={service.state}
                  />
                  <span
                    className={`h-2.5 w-2.5 rounded-full ${
                      service.state === 'running'
                        ? 'bg-emerald-500'
                        : service.state === 'stopped'
                        ? 'bg-amber-500'
                        : service.state === 'failed'
                        ? 'bg-red-500'
                        : 'bg-blue-500'
                    }`}
                    title={service.state}
                  />
                  <span className="text-xs uppercase text-muted-foreground">{service.state}</span>
                  {service.uptime_seconds != null && service.uptime_seconds > 0 && (
                    <span className="font-mono text-xs text-muted-foreground">
                      {Math.floor(service.uptime_seconds / 60)}m {Math.floor(service.uptime_seconds % 60)}s
                    </span>
                  )}
                  <button
                    onClick={() => onToggleService(service.name, service.state)}
                    disabled={!canToggle}
                    title={service.state === 'running' ? undefined : startConflict ?? undefined}
                    className={`px-3 py-1 text-xs text-white rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                      service.state === 'running' ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700'
                    }`}
                  >
                    {loading === `service-${service.name}` ? '...' : getServiceActionLabel(service)}
                  </button>
                  {service.name === 'xr-teleop' && (
                    <>
                      <button
                        onClick={onStartTeleoperation}
                        disabled={
                          service.state !== 'running' ||
                          loading === 'teleop-start' ||
                          !teleopRuntime?.ipc_online ||
                          teleopRuntime?.heartbeat?.START === true
                        }
                        className="px-3 py-1 text-xs text-white rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed bg-blue-600 hover:bg-blue-700"
                      >
                        {loading === 'teleop-start' ? '...' : 'Begin Teleop'}
                      </button>
                      <button
                        onClick={onStopTeleoperation}
                        disabled={
                          service.state !== 'running' ||
                          loading === 'teleop-stop' ||
                          !teleopRuntime?.ipc_online
                        }
                        className="px-3 py-1 text-xs text-white rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed bg-orange-600 hover:bg-orange-700"
                      >
                        {loading === 'teleop-stop' ? '...' : 'End Teleop'}
                      </button>
                    </>
                  )}
                </div>
                {service.name === 'xr-teleop' && teleopRuntime && (
                  <div className="w-full flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                    <span>IPC: {teleopRuntime.ipc_online ? 'online' : 'offline'}</span>
                    <span>READY: {teleopRuntime.heartbeat?.READY ? 'yes' : 'no'}</span>
                    <span>START: {teleopRuntime.heartbeat?.START ? 'yes' : 'no'}</span>
                    <span>STOP: {teleopRuntime.heartbeat?.STOP ? 'yes' : 'no'}</span>
                  </div>
                )}
              </div>
            );
          })}
          {teleServices.length === 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              Teleoperation services are not configured in the supervisor backend.
            </div>
          )}
        </div>
      </CollapsibleSection>

      {/* Section 2: Camera Monitor */}
      <CollapsibleSection title="Camera Monitor">
        <CameraMonitorSection
          cameraServices={cameraBridgeService ? [cameraBridgeService] : []}
        />
      </CollapsibleSection>

      {/* Section 3: Robot Temperatures */}
      <CollapsibleSection title="Robot Temperatures">
        <RobotTemperaturePanel
          service={temperatureMonitorService}
          temperatureState={robotTemperature}
          loading={loading === 'service-robot-temperature-monitor'}
          onToggleService={onToggleService}
          onViewLogs={onViewLogs}
          bare
        />
      </CollapsibleSection>
    </div>
  );
}
