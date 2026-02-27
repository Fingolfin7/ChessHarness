import { Outlet } from 'react-router-dom'
import NavBar from './NavBar.jsx'

export default function Layout() {
  return (
    <div className="app-layout">
      <NavBar />
      <div className="app-content">
        <Outlet />
      </div>
    </div>
  )
}
