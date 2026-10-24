// This file contains optional safe‑DOM utilities. You can import it where needed.

export const el = (tag: string, className?: string, text?: string): HTMLElement => {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined && text !== null) node.textContent = String(text)
  return node
}

export const pushLog = (containerId: string, level: string, message: string) => {
  const container = document.getElementById(containerId)
  if (!container) return
  const li = document.createElement('li')
  li.className = `log-line lvl-${level}`
  li.textContent = message
  container.appendChild(li)
  container.scrollTop = container.scrollHeight
}
