/**
 * Calling the referee robot to the table. HTTP 202 only means "request
 * accepted": the UI follows the call state by polling and never shows
 * "arrived" before the backend says so.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { describeError, newId, ttApi } from '../api/client';
import { DEFAULT_TABLE_ID, DEFAULT_WAYPOINT_ID, ROBOT_POLL_MS, STORAGE_KEYS, storageGet, storageSet } from '../config';
import type { RobotCall } from '../generated/contract';
import { ROBOT_CALL_TERMINAL } from '../labels';

export interface UseRobotCallResult {
  call: RobotCall | null;
  busy: boolean;
  request: () => Promise<void>;
  cancel: () => Promise<void>;
}

export function useRobotCall(matchId: string | null): UseRobotCallResult {
  const [call, setCall] = useState<RobotCall | null>(null);
  const [busy, setBusy] = useState(false);
  const requestId = useRef<string | null>(null);

  // Resume following a call made before a page reload.
  useEffect(() => {
    const saved = storageGet(STORAGE_KEYS.robotCallId);
    if (!saved) return;
    ttApi
      .getRobotCall(saved)
      .then((c) => {
        if (!matchId || !c.match_id || c.match_id === matchId) setCall(c);
      })
      .catch(() => storageSet(STORAGE_KEYS.robotCallId, null));
  }, [matchId]);

  const callId = call?.call_id ?? null;
  const terminal = call ? ROBOT_CALL_TERMINAL.has(call.state) : true;
  useEffect(() => {
    if (!callId || terminal) return undefined;
    const ctrl = new AbortController();
    const timer = window.setInterval(() => {
      ttApi
        .getRobotCall(callId, ctrl.signal)
        .then(setCall)
        .catch(() => undefined);
    }, ROBOT_POLL_MS);
    return () => {
      ctrl.abort();
      window.clearInterval(timer);
    };
  }, [callId, terminal]);

  const request = useCallback(async () => {
    // Keep the id until the backend answers, so a retry is the same request.
    requestId.current ??= newId();
    setBusy(true);
    try {
      const c = await ttApi.requestRobotCall({
        command_id: requestId.current,
        table_id: DEFAULT_TABLE_ID,
        named_waypoint_id: DEFAULT_WAYPOINT_ID,
        match_id: matchId,
      });
      requestId.current = null;
      setCall(c);
      storageSet(STORAGE_KEYS.robotCallId, c.call_id);
    } catch (err) {
      toast.error('Poziv sudije nije uspeo.', { description: describeError(err) });
    } finally {
      setBusy(false);
    }
  }, [matchId]);

  const cancel = useCallback(async () => {
    if (!call) return;
    setBusy(true);
    try {
      setCall(await ttApi.cancelRobotCall(call.call_id, newId()));
    } catch (err) {
      toast.error('Otkazivanje nije uspelo.', { description: describeError(err) });
    } finally {
      setBusy(false);
    }
  }, [call]);

  return { call, busy, request, cancel };
}
