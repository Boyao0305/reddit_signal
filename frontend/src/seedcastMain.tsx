import React from 'react'
import ReactDOM from 'react-dom/client'
import { SeedcastApp } from './features/seedcast'
import './seedcast.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <SeedcastApp />
  </React.StrictMode>,
)
