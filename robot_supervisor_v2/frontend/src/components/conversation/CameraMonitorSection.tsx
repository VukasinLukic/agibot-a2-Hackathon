import { AudioMonitorPanel } from './AudioMonitorPanel';
import { CameraStreamPanel } from './CameraStreamPanel';
import type { Service } from '@/api/types';

interface CameraMonitorSectionProps {
  cameraServices: Service[];
  showAudioMonitor?: boolean;
}

export function CameraMonitorSection({
  cameraServices,
  showAudioMonitor = false,
}: CameraMonitorSectionProps) {
  return (
    <div className="space-y-4">
      {showAudioMonitor && <AudioMonitorPanel />}
      {cameraServices.map((service) => (
        <CameraStreamPanel
          key={service.name}
          serviceName={service.name}
          displayName={service.display_name}
        />
      ))}
    </div>
  );
}
