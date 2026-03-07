import { useMemo, useRef, useState, useEffect } from 'react'
import { Chessboard } from 'react-chessboard'
import { Chess } from 'chess.js'

const PROMOTION_PIECES = ['q', 'r', 'b', 'n']
const PIECE_LABELS = {
  q: 'Queen',
  r: 'Rook',
  b: 'Bishop',
  n: 'Knight',
}

function buildGame(fen) {
  return new Chess(fen === 'start' ? undefined : fen)
}

function isPromotionMove(game, sourceSquare, targetSquare) {
  const piece = game.get(sourceSquare)
  if (!piece || piece.type !== 'p') return false
  return (piece.color === 'w' && targetSquare[1] === '8') || (piece.color === 'b' && targetSquare[1] === '1')
}

export default function Board({
  fen,
  lastMove,
  flipped = false,
  interactive = false,
  onMove = null,
}) {
  const wrapperRef = useRef(null)
  const [boardWidth, setBoardWidth] = useState(500)
  const [selectedSquare, setSelectedSquare] = useState(null)
  const [pendingPromotion, setPendingPromotion] = useState(null)

  const game = useMemo(() => buildGame(fen), [fen])
  const legalTargets = useMemo(() => {
    if (!selectedSquare) return []
    return game.moves({ square: selectedSquare, verbose: true }).map(move => move.to)
  }, [game, selectedSquare])

  useEffect(() => {
    if (!wrapperRef.current) return
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        setBoardWidth(Math.floor(entry.contentRect.width))
      }
    })
    ro.observe(wrapperRef.current)
    setBoardWidth(Math.floor(wrapperRef.current.offsetWidth))
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    setSelectedSquare(null)
    setPendingPromotion(null)
  }, [fen])

  const squareStyles = {}
  if (lastMove) {
    squareStyles[lastMove.from] = { backgroundColor: 'rgba(255, 255, 100, 0.45)' }
    squareStyles[lastMove.to] = { backgroundColor: 'rgba(255, 255, 100, 0.45)' }
  }
  if (selectedSquare) {
    squareStyles[selectedSquare] = {
      ...(squareStyles[selectedSquare] || {}),
      boxShadow: 'inset 0 0 0 4px rgba(44, 62, 80, 0.65)',
    }
  }
  for (const square of legalTargets) {
    squareStyles[square] = {
      ...(squareStyles[square] || {}),
      boxShadow: 'inset 0 0 0 9999px rgba(52, 152, 219, 0.20)',
    }
  }

  const submitMove = (sourceSquare, targetSquare, promotion = 'q') => {
    if (!interactive || !onMove) return false
    try {
      const candidate = buildGame(fen)
      const moved = candidate.move({
        from: sourceSquare,
        to: targetSquare,
        promotion,
      })
      if (!moved) return false
      const uci = `${sourceSquare}${targetSquare}${moved.promotion || ''}`
      onMove(uci)
      return true
    } catch {
      return false
    }
  }

  const queuePromotion = (sourceSquare, targetSquare) => {
    const piece = game.get(sourceSquare)
    if (!piece) return false
    setPendingPromotion({
      from: sourceSquare,
      to: targetSquare,
      color: piece.color,
    })
    return true
  }

  const tryMove = (sourceSquare, targetSquare) => {
    if (!sourceSquare || !targetSquare) return false
    if (isPromotionMove(game, sourceSquare, targetSquare)) {
      return queuePromotion(sourceSquare, targetSquare)
    }
    const accepted = submitMove(sourceSquare, targetSquare)
    if (accepted) setSelectedSquare(null)
    return accepted
  }

  const handlePieceDrop = (sourceSquare, targetSquare) => {
    const accepted = tryMove(sourceSquare, targetSquare)
    return accepted
  }

  const handleSquareClick = (square) => {
    if (!interactive || pendingPromotion) return

    const piece = game.get(square)
    const turn = game.turn()

    if (selectedSquare) {
      if (square === selectedSquare) {
        setSelectedSquare(null)
        return
      }
      const moved = tryMove(selectedSquare, square)
      if (moved) return
      if (piece && piece.color === turn) {
        setSelectedSquare(square)
        return
      }
      setSelectedSquare(null)
      return
    }

    if (piece && piece.color === turn) {
      setSelectedSquare(square)
    }
  }

  const handlePromotionSelect = (promotion) => {
    if (!pendingPromotion) return
    const accepted = submitMove(pendingPromotion.from, pendingPromotion.to, promotion)
    if (accepted) {
      setPendingPromotion(null)
      setSelectedSquare(null)
    }
  }

  return (
    <div ref={wrapperRef} className="board-wrapper board-wrapper--interactive-parent">
      <Chessboard
        position={fen}
        boardWidth={boardWidth}
        boardOrientation={flipped ? 'black' : 'white'}
        arePiecesDraggable={interactive}
        onPieceDrop={handlePieceDrop}
        onSquareClick={handleSquareClick}
        customSquareStyles={squareStyles}
        customDarkSquareStyle={{ backgroundColor: '#b58863' }}
        customLightSquareStyle={{ backgroundColor: '#f0d9b5' }}
      />
      {pendingPromotion && (
        <div className="promotion-overlay">
          <div className="promotion-dialog">
            <div className="promotion-title">Choose promotion</div>
            <div className="promotion-options">
              {PROMOTION_PIECES.map(piece => (
                <button
                  key={piece}
                  type="button"
                  className="promotion-option"
                  onClick={() => handlePromotionSelect(piece)}
                >
                  <span className="promotion-piece">
                    {pendingPromotion.color === 'w'
                      ? { q: 'Q', r: 'R', b: 'B', n: 'N' }[piece]
                      : { q: 'q', r: 'r', b: 'b', n: 'n' }[piece]}
                  </span>
                  <span>{PIECE_LABELS[piece]}</span>
                </button>
              ))}
            </div>
            <button type="button" className="promotion-cancel" onClick={() => setPendingPromotion(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
