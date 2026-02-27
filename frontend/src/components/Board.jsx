import { useRef, useState, useEffect } from 'react'
import { Chessboard } from 'react-chessboard'

export default function Board({ fen, lastMove, flipped = false }) {
  const wrapperRef = useRef(null)
  const [boardWidth, setBoardWidth] = useState(500)

  useEffect(() => {
    if (!wrapperRef.current) return
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        setBoardWidth(Math.floor(entry.contentRect.width))
      }
    })
    ro.observe(wrapperRef.current)
    // Set initial size immediately
    setBoardWidth(Math.floor(wrapperRef.current.offsetWidth))
    return () => ro.disconnect()
  }, [])

  const squareStyles = {}
  if (lastMove) {
    squareStyles[lastMove.from] = { backgroundColor: 'rgba(255, 255, 100, 0.45)' }
    squareStyles[lastMove.to]   = { backgroundColor: 'rgba(255, 255, 100, 0.45)' }
  }

  return (
    <div ref={wrapperRef} className="board-wrapper">
      <Chessboard
        position={fen}
        boardWidth={boardWidth}
        boardOrientation={flipped ? 'black' : 'white'}
        arePiecesDraggable={false}
        customSquareStyles={squareStyles}
        customDarkSquareStyle={{ backgroundColor: '#b58863' }}
        customLightSquareStyle={{ backgroundColor: '#f0d9b5' }}
      />
    </div>
  )
}
