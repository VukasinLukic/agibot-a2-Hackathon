import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Toaster } from 'sonner'
import './index.css'
import App from './App.tsx'
import { TableTennisPage } from './features/table-tennis/TableTennisPage'

// Players open the referee app on their phones at /#/stoni-tenis (no Supervisor shell).
const tableTennisOnly = window.location.hash.startsWith('#/stoni-tenis')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {tableTennisOnly ? <TableTennisPage /> : <App />}
    <Toaster />
  </StrictMode>,
)
