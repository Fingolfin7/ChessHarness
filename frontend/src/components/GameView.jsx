import { useEffect, useState } from 'react'
import Board from './Board.jsx'
import PlayerPanel from './PlayerPanel.jsx'
import MoveHistory from './MoveHistory.jsx'
import ReasoningPanel from './ReasoningPanel.jsx'

function resultText(result) {
  if (!result) return null
  if (result.reason === 'interrupted') return 'Game stopped by user'
  if (result.winner) return `${result.winner} wins by ${result.reason.replace(/_/g, ' ')}`
  return `Draw - ${result.reason.replace(/_/g, ' ')}`
}

export default function GameView({ state, onStop, onNewGame, onRematch, onSubmitHumanMove }) {
  const {
    players, fen, lastMove, turn, thinking, reasoning, moves,
    plies, awaitingHumanInput, invalidAttempt, result, error, phase,
  } = state
  const isOver = phase === 'over'
  const [viewIndex, setViewIndex] = useState(null)
  const [flipped, setFlipped] = useState(false)

  useEffect(() => {
    if (viewIndex === null) return
    if (viewIndex === plies.length - 2) setViewIndex(null)
  }, [plies.length])

  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'ArrowLeft') {
        setViewIndex(v => {
          if (v === null) return plies.length > 0 ? plies.length - 1 : null
          return Math.max(0, v - 1)
        })
      }
      if (e.key === 'ArrowRight') {
        setViewIndex(v => {
          if (v === null) return null
          return v >= plies.length - 1 ? null : v + 1
        })
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [plies.length])

  const isLive = viewIndex === null
  const currentPly = isLive ? null : plies[viewIndex]

  const displayFen = currentPly ? currentPly.fen_after : fen
  const displayLastMove = currentPly ? { from: currentPly.from, to: currentPly.to } : lastMove

  const displayReasoning = (() => {
    if (isLive) return reasoning
    const out = { white: '', black: '' }
    for (let i = viewIndex; i >= 0; i--) {
      const p = plies[i]
      if (!out[p.color]) out[p.color] = p.reasoning
      if (out.white && out.black) break
    }
    return out
  })()

  const isThinkingWhite = isLive && turn === 'white' && thinking
  const isThinkingBlack = isLive && turn === 'black' && thinking
  const isAwaitingWhite = isLive && awaitingHumanInput?.color === 'white'
  const isAwaitingBlack = isLive && awaitingHumanInput?.color === 'black'

  const goToStart = () => setViewIndex(0)
  const goBack = () => setViewIndex(v => v === null ? plies.length - 1 : Math.max(0, v - 1))
  const goForward = () => setViewIndex(v => v === null ? null : (v >= plies.length - 1 ? null : v + 1))
  const goToLive = () => setViewIndex(null)
  const goToPly = (idx) => setViewIndex(idx)

  const navLabel = isLive
    ? 'Live'
    : `Move ${currentPly.moveNumber}${currentPly.color === 'white' ? 'w' : 'b'} - ${currentPly.san}`

  const lastWhiteSan = [...moves].reverse().find(m => m.white)?.white?.san
  const lastBlackSan = [...moves].reverse().find(m => m.black)?.black?.san
  const humanTurnColor = awaitingHumanInput?.color
  const boardInteractive = isLive && !!humanTurnColor

  const handleBoardMove = (move) => {
    if (!humanTurnColor) return
    onSubmitHumanMove(move, humanTurnColor)
  }

  return (
    <div className="game-layout">
      <header className="game-header">
        <div className="header-controls">
          <button className="btn" onClick={() => setFlipped(f => !f)} title="Flip board">Flip</button>
          {!isOver && <button className="btn btn-stop" onClick={onStop}>Stop Game</button>}
          {isOver && <button className="btn btn-rematch" onClick={onRematch}>Rematch</button>}
          {isOver && <button className="btn btn-new" onClick={onNewGame}>New Game</button>}
        </div>
      </header>

      {result && (
        <div className={`result-banner ${result.result === '1/2-1/2' ? 'draw' : ''}`}>
          {resultText(result)}
        </div>
      )}
      {error && <div className="error-banner">{error}</div>}

      <div className="game-main">
        <div className="board-col">
          <Board
            fen={displayFen}
            lastMove={displayLastMove}
            flipped={flipped}
            interactive={boardInteractive}
            onMove={handleBoardMove}
          />
        </div>

        <div className="sidebar">
          <PlayerPanel
            color="black"
            name={players.black?.name}
            playerType={players.black?.type}
            isActive={turn === 'black' && !isOver}
            isThinking={isThinkingBlack}
            isAwaitingInput={isAwaitingBlack}
            invalidAttempt={invalidAttempt?.color === 'black' ? invalidAttempt : null}
            lastMoveSan={lastBlackSan}
          />

          <MoveHistory
            moves={moves}
            plies={plies}
            viewIndex={viewIndex}
            onNavigate={goToPly}
            pgn={result?.pgn}
            goToStart={goToStart}
            goBack={goBack}
            goForward={goForward}
            goToLive={goToLive}
            isLive={isLive}
            navLabel={navLabel}
          />

          <PlayerPanel
            color="white"
            name={players.white?.name}
            playerType={players.white?.type}
            isActive={turn === 'white' && !isOver}
            isThinking={isThinkingWhite}
            isAwaitingInput={isAwaitingWhite}
            invalidAttempt={invalidAttempt?.color === 'white' ? invalidAttempt : null}
            lastMoveSan={lastWhiteSan}
          />
        </div>
      </div>

      <div className="reasoning-row">
        <ReasoningPanel
          color="white"
          name={players.white?.name}
          reasoning={displayReasoning.white}
          isThinking={isThinkingWhite}
          isReviewing={!isLive && currentPly?.color === 'white'}
        />
        <ReasoningPanel
          color="black"
          name={players.black?.name}
          reasoning={displayReasoning.black}
          isThinking={isThinkingBlack}
          isReviewing={!isLive && currentPly?.color === 'black'}
        />
      </div>
    </div>
  )
}
