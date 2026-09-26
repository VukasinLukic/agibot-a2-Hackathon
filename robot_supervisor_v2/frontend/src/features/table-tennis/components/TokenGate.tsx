import { useState } from 'react';
import { STORAGE_KEYS, storageGet, storageSet } from '../config';

/**
 * Shown when the backend asks for a token (the robot runs with token auth).
 * The token stays in this browser only and is sent as an Authorization header.
 */
export function TokenGate() {
  const [value, setValue] = useState('');
  const hadToken = Boolean(storageGet(STORAGE_KEYS.operatorToken));

  const save = () => {
    const token = value.trim();
    if (!token) return;
    storageSet(STORAGE_KEYS.operatorToken, token);
    window.location.reload(); // reconnect every request and the live stream with the new token
  };

  return (
    <div className="tt">
      <div className="mx-auto w-full max-w-md space-y-5 px-4 pb-10 pt-4">
        <p className="tt-display text-2xl font-extrabold leading-none">
          <span className="text-[var(--tt-red)]">TITAN</span>SUDIJA
        </p>
        <h1 className="tt-display text-3xl font-extrabold">Pristupni kod</h1>
        <p className="text-[var(--tt-grey)]">
          {hadToken
            ? 'Sačuvani kod više ne važi. Upiši novi kod koji ti je dao tim.'
            : 'Titan prima komande samo uz kod. Upiši kod koji ti je dao tim; telefon ga pamti.'}
        </p>
        <input
          aria-label="Pristupni kod"
          className="tt-input"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && save()}
        />
        <button type="button" disabled={!value.trim()} onClick={save} className="tt-btn tt-btn-primary w-full min-h-16 text-xl">
          Sačuvaj i nastavi
        </button>
      </div>
    </div>
  );
}
