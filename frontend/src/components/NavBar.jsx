import { NavLink } from 'react-router-dom'

export default function NavBar() {
  return (
    <nav className="app-nav">
      <NavLink to="/" className="app-nav-logo" title="Home">
        ♔ ChessHarness
      </NavLink>
      <div className="app-nav-links">
        <NavLink
          to="/game"
          className={({ isActive }) => `app-nav-link${isActive ? ' app-nav-link--active' : ''}`}
        >
          Game
        </NavLink>
        <NavLink
          to="/tournament/setup"
          className={({ isActive }) => `app-nav-link${isActive ? ' app-nav-link--active' : ''}`}
        >
          New Tournament
        </NavLink>
        <NavLink
          to="/tournament"
          end
          className={({ isActive }) => `app-nav-link${isActive ? ' app-nav-link--active' : ''}`}
        >
          Live Tournament
        </NavLink>
      </div>
    </nav>
  )
}
