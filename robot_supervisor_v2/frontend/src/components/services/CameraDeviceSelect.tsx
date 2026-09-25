import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type { CameraDevice } from '@/api/types';

interface CameraDeviceSelectProps {
  value: string;
  onChange: (devicePath: string) => void | Promise<void>;
  disabled?: boolean;
  selectClassName?: string;
  buttonClassName?: string;
}

export function CameraDeviceSelect({
  value,
  onChange,
  disabled = false,
  selectClassName,
  buttonClassName,
}: CameraDeviceSelectProps) {
  const [videoDevices, setVideoDevices] = useState<CameraDevice[]>([]);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanned, setScanned] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const loadInitialCameraDevices = async () => {
      try {
        const videoResponse = await api.listVideoDevices();
        if (!cancelled) {
          setVideoDevices(videoResponse.devices ?? []);
          setScanned(true);
        }
      } catch (err) {
        if (!cancelled) {
          console.error('Failed to load camera devices:', err);
          setScanned(true);
        }
      }
    };

    void loadInitialCameraDevices();
    return () => {
      cancelled = true;
    };
  }, []);

  const scanCameraDevices = async () => {
    setScanLoading(true);
    try {
      const videoResponse = await api.listVideoDevices({ force: true });
      setVideoDevices(videoResponse.devices ?? []);
      setScanned(true);
    } catch (err) {
      console.error('Failed to load camera devices:', err);
      setScanned(true);
      toast.error(`Failed to scan cameras: ${err}`);
    } finally {
      setScanLoading(false);
    }
  };

  const hasSelectedCameraOption = Boolean(
    value && videoDevices.some((device) => device.path === value)
  );
  const cameraOptions =
    videoDevices.length > 0
      ? hasSelectedCameraOption || !value
        ? videoDevices
        : [
            {
              path: value,
              name: value,
              index: -1,
            } as CameraDevice,
            ...videoDevices,
          ]
      : [{
          path: value || '',
          name: value || 'No camera devices detected',
          index: -1,
        } as CameraDevice];

  return (
    <>
      <select
        value={value}
        onChange={(event) => void onChange(event.target.value)}
        disabled={disabled || scanLoading}
        className={selectClassName ?? 'text-xs border border-border bg-background text-foreground rounded-md px-2 py-1 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring/40 max-w-[220px]'}
        title={
          !scanned
            ? 'Loading camera devices'
            : videoDevices.length === 0
            ? 'No camera devices were reported by the backend'
            : 'Use Rescan to refresh the device list'
        }
      >
        {cameraOptions.map((device) => (
          <option key={device.path} value={device.path} className="bg-background text-foreground">
            {device.name || device.path}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => void scanCameraDevices()}
        disabled={disabled || scanLoading}
        className={buttonClassName ?? 'px-3 py-1 text-xs rounded-full font-medium transition-colors bg-slate-600 hover:bg-slate-700 text-white disabled:opacity-50 disabled:cursor-not-allowed'}
        title="Scan camera devices now"
      >
        {scanLoading ? 'Scanning...' : scanned ? 'Rescan' : 'Scan'}
      </button>
    </>
  );
}
