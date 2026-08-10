// Ponto de entrada do frontend: monta o componente <App /> na div #root
// (ver frontend/index.html) e aplica os estilos globais (Tailwind).
import React from "react"
import { createRoot } from "react-dom/client"
import { App } from "./App"
import "./styles.css"

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
