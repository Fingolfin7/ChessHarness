import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppProvider } from './context/AppContext.jsx'
import Layout from './components/Layout.jsx'
import GamePage from './pages/GamePage.jsx'
import TournamentPage from './components/tournament/TournamentPage.jsx'
import TournamentSetup from './components/tournament/TournamentSetup.jsx'

export default function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Navigate to="/game" replace />} />
            <Route path="/game" element={<GamePage />} />
            <Route path="/tournament/setup" element={<TournamentSetup />} />
            <Route path="/tournament" element={<TournamentPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProvider>
  )
}
