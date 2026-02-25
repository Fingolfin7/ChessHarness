import Board from './Board.jsx'
import PlayerPanel from './PlayerPanel.jsx'
import MoveHistory from './MoveHistory.jsx'
import ReasoningPanel from './ReasoningPanel.jsx'

function resultText(result) {
  if (!result) return null
  if (result.reason === 'interrupted') return 'Game stopped by user'
  if (result.winner) {
    const reason = result.reason.replace(/_/g, ' ')
    return `${result.winner} wins by ${reason}`
  }
  const reason = result.reason.replace(/_/g, ' ')
  return `Draw — ${reason}`
}

export default function GameView({ state, onStop, onNewGame }) {
  const { players, fen, lastMove, turn, thinking, reasoning, moves,
          invalidAttempt, result, error, phase } = state
  const isOver = phase === 'over'

  const lastWhiteSan = [...moves].reverse().find(m => m.white)?.white?.san
  const lastBlackSan = [...moves].reverse().find(m => m.black)?.black?.san

  return (
    <div className="game-layout">

      {/* ── Header ── */}
      <header className="game-header">
        <span className="logo">♔ ChessHarness</span>
        <div className="header-controls">
          {!isOver && (
            <button className="btn btn-stop" onClick={onStop}>Stop Game</button>
          )}
          {isOver && (
            <button className="btn btn-new" onClick={onNewGame}>New Game</button>
          )}
        </div>
      </header>

      {/* ── Result banner ── */}
      {result && (
        <div className={`result-banner ${result.result === '1/2-1/2' ? 'draw' : ''}`}>
          {resultText(result)}
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      {/* ── Main area ── */}
      <div className="game-main">
        <div className="board-col">
          <Board fen={fen} lastMove={lastMove} />
        </div>

        <div className="sidebar">
          <div className="player-panels">
            <PlayerPanel
              color="white"
              name={players.white?.name}
              isActive={turn === 'white' && !isOver}
              isThinking={turn === 'white' && thinking}
              invalidAttempt={invalidAttempt?.color === 'white' ? invalidAttempt : null}
              lastMoveSan={lastWhiteSan}
            />
            <PlayerPanel
              color="black"
              name={players.black?.name}
              isActive={turn === 'black' && !isOver}
              isThinking={turn === 'black' && thinking}
              invalidAttempt={invalidAttempt?.color === 'black' ? invalidAttempt : null}
              lastMoveSan={lastBlackSan}
            />
          </div>

          <MoveHistory moves={moves} />
        </div>
      </div>

      {/* ── Reasoning row ── */}
      <div className="reasoning-row">
        <ReasoningPanel
          color="white"
          name={players.white?.name}
          reasoning={reasoning.white}
          isThinking={turn === 'white' && thinking}
        />
        <ReasoningPanel
          color="black"
          name={players.black?.name}
          reasoning={reasoning.black}
          isThinking={turn === 'black' && thinking}
        />
      </div>

    </div>
  )
}
