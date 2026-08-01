---
name: Dashboard Component Standard
description: Guidelines for developing new features and UI components for the CloudX Horizon Dashboard (React + Vite).
---

# Dashboard Component Standard

When building new UI features for the CloudX dashboard (e.g., noVNC Console, Network Topology, Image Drag & Drop), follow these standards to maintain high quality and visual consistency.

## 1. Aesthetics and Styling
- Adhere strictly to the established **premium dark theme**. Utilize the existing CSS variables defined in `src/index.css`.
- Avoid hardcoded colors unless extending the existing palette. Ensure new components look sleek, vibrant, and match the "CloudX" aesthetic.
- Include micro-animations or hover effects for interactive elements to make the interface feel responsive.

## 2. API Integration
- Centralize all backend communication in `src/api.js`. Do not make raw `fetch` or `axios` calls directly from React components.
- Use standard asynchronous patterns (async/await) and handle loading states effectively (e.g., displaying spinners or skeleton loaders while fetching).
- Provide clear error feedback to the user via toast notifications or inline error messages when an API call fails.

## 3. State Management
- Utilize the global context (`src/context.jsx`) for application-wide state (e.g., authenticated user, active project, token).
- Keep component state local where possible.

## 4. Real-time Features
- For features requiring real-time updates (like Log Viewer or status badges), leverage the existing WebSocket infrastructure.
- Always clean up event listeners and intervals (e.g., `clearInterval`) in `useEffect` cleanup functions to prevent memory leaks.

## 5. Third-party Libraries
- When complex visual components are needed (like Network Topology), prefer established libraries (e.g., `reactflow`, `cytoscape.js`) but ensure they are styled to match the dark theme.
