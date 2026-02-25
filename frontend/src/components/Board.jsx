import { Chessboard } from 'react-chessboard'

export default function Board({ fen, lastMove }) {
  const squareStyles = {}
  if (lastMove) {
    squareStyles[lastMove.from] = { backgroundColor: 'rgba(255, 255, 100, 0.45)' }
    squareStyles[lastMove.to]   = { backgroundColor: 'rgba(255, 255, 100, 0.45)' }
  }

  return (
    <div className="board-wrapper">
      <Chessboard
        position={fen}
        boardWidth={500}
        arePiecesDraggable={false}
        customSquareStyles={squareStyles}
        customDarkSquareStyle={{ backgroundColor: '#b58863' }}
        customLightSquareStyle={{ backgroundColor: '#f0d9b5' }}
      />
    </div>
  )
}
